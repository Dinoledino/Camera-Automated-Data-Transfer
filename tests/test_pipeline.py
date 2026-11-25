"""Recouvrement transfert / vérification, et vidange découpée en tranches."""
import errno
import os
import threading
import time

import pytest

import cadt.downloader as dl
from cadt.db import Database
from cadt.downloader import DirectorySource, DiskFull, Downloader, SourceDisconnected
from cadt.watcher import Watcher
from tests.test_watcher import FakeLister

FOLDER = "/DCIM/100NC850"


@pytest.fixture
def env(tmp_path):
    cam = tmp_path / "card"
    (cam / FOLDER.lstrip("/")).mkdir(parents=True)
    db = Database(tmp_path / "cadt.db")
    yield cam, db, tmp_path / "sd"
    db.close()


def shoot(cam, db, name, t, size=20_000):
    data = os.urandom(size)
    (cam / FOLDER.lstrip("/") / name).write_bytes(data)
    return db.add_detected(FOLDER, name, size, t)[0], data


def parts(sd):
    return list(sd.rglob("*.part")) if sd.exists() else []


class SlowSource(DirectorySource):
    def __init__(self, root, delay):
        super().__init__(root)
        self.delay = delay

    def iter_chunks(self, folder, name):
        time.sleep(self.delay)
        yield from super().iter_chunks(folder, name)


def test_verification_runs_in_worker_thread_and_overlaps(env, monkeypatch):
    cam, db, sd = env
    photos = [shoot(cam, db, f"DSC_{i:04d}.NEF", f"2026-11-25T10:00:{i:02d}") for i in range(5)]
    threads = []
    real_verify = Downloader._verify_on_disk

    def slow_verify(part):
        threads.append(threading.current_thread().name)
        time.sleep(0.15)
        return real_verify(part)

    monkeypatch.setattr(Downloader, "_verify_on_disk", staticmethod(slow_verify))
    t = time.monotonic()
    outs = Downloader(db, SlowSource(cam, 0.15), sd).drain()
    pipelined = time.monotonic() - t

    assert all(o.ok for o in outs) and len(outs) == 5
    assert all(name.startswith("verify") for name in threads)
    for p, data in photos:
        assert open(db.get(p.id).local_path, "rb").read() == data
    assert parts(sd) == []
    # 5 × (0,15 transfert + 0,15 vérification) = 1,5 s en séquentiel ; ~0,9 s en recouvrement
    assert pipelined < 1.25


def test_sequential_mode_verifies_in_main_thread(env, monkeypatch):
    cam, db, sd = env
    shoot(cam, db, "DSC_0001.NEF", "2026-11-25T10:00:00")
    threads = []
    real_verify = Downloader._verify_on_disk
    monkeypatch.setattr(Downloader, "_verify_on_disk",
                        staticmethod(lambda part: threads.append(threading.current_thread().name) or real_verify(part)))
    assert Downloader(db, DirectorySource(cam), sd, pipelined=False).drain()[0].ok
    assert threads == [threading.main_thread().name]


def test_disconnect_while_previous_photo_is_verifying(env):
    """La photo N (transférée) est finalisée et vérifiée ; la N+1 repart dans la file."""
    cam, db, sd = env
    first, data = shoot(cam, db, "DSC_0001.NEF", "2026-11-25T10:00:00")
    second, _ = shoot(cam, db, "DSC_0002.NEF", "2026-11-25T10:00:01")

    class UnplugAfterFirst(DirectorySource):
        calls = 0

        def iter_chunks(self, folder, name):
            UnplugAfterFirst.calls += 1
            if UnplugAfterFirst.calls > 1:
                raise SourceDisconnected("câble débranché")
            yield from super().iter_chunks(folder, name)

    with pytest.raises(SourceDisconnected):
        Downloader(db, UnplugAfterFirst(cam), sd).drain()

    assert db.get(first.id).state == "verified"
    assert open(db.get(first.id).local_path, "rb").read() == data
    p2 = db.get(second.id)
    assert p2.state == "detected" and p2.attempts == 0
    assert parts(sd) == []


def test_disk_full_during_verification_stops_cleanly(env, monkeypatch):
    """fsync de la photo N échoue (carte pleine) alors que la N+1 est déjà transférée."""
    cam, db, sd = env
    first, _ = shoot(cam, db, "DSC_0001.NEF", "2026-11-25T10:00:00")
    second, _ = shoot(cam, db, "DSC_0002.NEF", "2026-11-25T10:00:01")
    third, _ = shoot(cam, db, "DSC_0003.NEF", "2026-11-25T10:00:02")
    real_verify = Downloader._verify_on_disk

    def full_on_first(part):
        if "DSC_0001" in part.name:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_verify(part)

    monkeypatch.setattr(Downloader, "_verify_on_disk", staticmethod(full_on_first))
    with pytest.raises(DiskFull):
        Downloader(db, DirectorySource(cam), sd).drain()

    for p in (first, second, third):
        q = db.get(p.id)
        assert q.state == "detected" and q.attempts == 0, (p.name, q)
    assert db.get(first.id).last_error == "microSD pleine"
    assert parts(sd) == []
    assert not any(sd.rglob("DSC_*")) if sd.exists() else True


def test_hash_mismatch_in_pipelined_mode(env, monkeypatch):
    cam, db, sd = env
    a, _ = shoot(cam, db, "DSC_0001.NEF", "2026-11-25T10:00:00")
    b, _ = shoot(cam, db, "DSC_0002.NEF", "2026-11-25T10:00:01")
    real = dl.sha256_file
    monkeypatch.setattr(dl, "sha256_file",
                        lambda path, drop_cache=True: "0" * 64 if "DSC_0001" in str(path) else real(path))
    outs = Downloader(db, DirectorySource(cam), sd).drain()
    assert {o.photo.id: o.ok for o in outs} == {a.id: False, b.id: True}
    assert db.get(a.id).state == "detected" and db.get(a.id).next_attempt_at is not None
    assert parts(sd) == []


def test_on_outcome_is_called_for_each_photo(env):
    cam, db, sd = env
    for i in range(3):
        shoot(cam, db, f"DSC_000{i}.NEF", f"2026-11-25T10:00:0{i}")
    seen = []
    Downloader(db, DirectorySource(cam), sd, on_outcome=lambda o: seen.append(o.photo.name)).drain()
    assert sorted(seen) == ["DSC_0000.NEF", "DSC_0001.NEF", "DSC_0002.NEF"]


# ------------------------------------------------------------------ tranches

class StepTimer:
    """Horloge qui avance d'une seconde à chaque photo terminée."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def tick(self, _outcome):
        self.t += 1


def test_drain_stops_taking_photos_after_budget(env):
    cam, db, sd = env
    for i in range(5):
        shoot(cam, db, f"DSC_000{i}.NEF", f"2026-11-25T10:00:0{i}")
    timer = StepTimer()
    d = Downloader(db, DirectorySource(cam), sd, pipelined=False, timer=timer, on_outcome=timer.tick)
    assert len(d.drain(max_seconds=2)) == 2
    assert db.count_unsaved() == 3
    assert len(d.drain()) == 3


def test_new_jpeg_overtakes_waiting_raws(tmp_path):
    """Pendant une longue vidange de NEF, le JPEG d'une nouvelle prise passe devant."""
    lister = FakeLister(tmp_path / "card")
    with Database(tmp_path / "c.db") as db:
        for i in range(6):
            lister.put(f"DSC_000{i}.NEF", os.urandom(1000))
        timer = StepTimer()
        order = []

        def on_outcome(o):
            timer.tick(o)
            order.append(o.photo.name)

        d = Downloader(db, DirectorySource(tmp_path / "card"), tmp_path / "sd", pipelined=False,
                       timer=timer, on_outcome=on_outcome)
        w = Watcher(db, lister, d, stable_polls=1, drain_budget_seconds=2, drain_budget_raw_only_seconds=2)
        w.poll_once()                                   # 2 NEF sauvegardés, 4 en attente
        lister.put("DSC_0009.JPG", os.urandom(500))     # nouvelle prise
        while db.count_unsaved() or len(order) < 7:
            w.poll_once()
        assert order.index("DSC_0009.JPG") == 2         # juste après la première tranche


def test_drain_budget_depends_on_jpeg_in_recent_shots(tmp_path):
    lister = FakeLister(tmp_path / "card")
    with Database(tmp_path / "c.db") as db:
        w = Watcher(db, lister, Downloader(db, DirectorySource(tmp_path / "card"), tmp_path / "sd"),
                    drain_budget_seconds=10, drain_budget_raw_only_seconds=60, recent_window=3)
        assert w.current_drain_budget() == 60                     # rien encore
        db.add_detected(FOLDER, "DSC_0001.JPG", 1, "2026-11-25T10:00:00")
        assert w.current_drain_budget() == 10                     # RAW + JPEG
        for i in range(2, 5):
            db.add_detected(FOLDER, f"DSC_000{i}.NEF", 1, f"2026-11-25T10:00:0{i}")
        assert w.current_drain_budget() == 60                     # le JPEG est sorti de la fenêtre
