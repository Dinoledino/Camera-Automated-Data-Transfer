"""Emplacements de stockage multiples, téléphone plein."""
import os
from pathlib import Path

import pytest

from cadt.camera import DirectoryLister
from cadt.cleaner import CameraCleaner
from cadt.db import Database
from cadt.downloader import DirectorySource, DiskFull, Downloader
from cadt.sessions import move_photos
from cadt.simulator import SimulatedCamera
from cadt.storage import StoragePool
from cadt.watcher import Watcher


class FakeDisks:
    """Espace libre simulé par emplacement ; diminue quand on y écrit des photos."""

    def __init__(self, **free):
        self.free = {k: v for k, v in free.items()}

    def __call__(self, root: Path):
        used = sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) if root.exists() else 0
        free = self.free[root.name] - used
        return (10 ** 9, used, free)


@pytest.fixture
def env(tmp_path):
    card = tmp_path / "card"
    db = Database(tmp_path / "cadt.db")
    sim = SimulatedCamera(card, raw_size=1000, slots=2)
    yield db, sim, card, tmp_path
    db.close()


def pool(tmp_path, disks, reserve=500):
    return StoragePool([tmp_path / "interne", tmp_path / "sd"], reserve_bytes=reserve, usage_fn=disks)


def test_choose_fills_in_order():
    disks = FakeDisks(interne=3000, sd=10_000)
    p = StoragePool([Path("/x/interne"), Path("/x/sd")], reserve_bytes=500, usage_fn=lambda r: (0, 0, disks.free[r.name]))
    assert p.choose(1000).name == "interne"
    assert p.choose(2600).name == "sd"           # 3000 - 500 de marge < 2600
    assert p.choose(20_000) is None
    assert p.free_for_photos() == 2500 + 9500


def test_root_of():
    p = StoragePool(["/m/interne", "/m/sd"], usage_fn=lambda r: (0, 0, 0))
    assert p.root_of("/m/sd/2026-11-25/DSC_0001.NEF") == Path("/m/sd")
    assert p.root_of("/ailleurs/x") == Path("/m/interne")


def test_photos_spill_to_second_location(env):
    db, sim, card, tmp = env
    for _ in range(4):
        sim.shoot()
    sp = pool(tmp, FakeDisks(interne=2600, sd=100_000))   # 2 photos tiennent dans « interne »
    w = Watcher(db, DirectoryLister(card), Downloader(db, DirectorySource(card), sp), stable_polls=1)
    assert w.poll_once().saved == 4
    roots = [sp.root_of(r["local_path"]).name for r in db.conn.execute("SELECT local_path FROM photos ORDER BY id")]
    assert roots == ["interne", "interne", "sd", "sd"]


def test_full_phone_stops_copy_and_keeps_photos_on_camera(env):
    db, sim, card, tmp = env
    for _ in range(3):
        sim.shoot()
    sp = pool(tmp, FakeDisks(interne=1600, sd=1600))      # 1 photo par emplacement
    src = DirectorySource(card)
    w = Watcher(db, DirectoryLister(card), Downloader(db, src, sp), stable_polls=1,
                cleaner=CameraCleaner(db, src))
    with pytest.raises(DiskFull):
        w.poll_once()
    w.cleaner.run(DirectoryLister(card).list_files())
    states = [r["state"] for r in db.conn.execute("SELECT state FROM photos ORDER BY id")]
    assert states == ["verified", "verified", "detected"]
    third = db.conn.execute("SELECT * FROM photos ORDER BY id DESC LIMIT 1").fetchone()
    assert third["attempts"] == 0 and third["last_error"] == "mémoire du téléphone pleine"
    left = sorted(p.name for p in card.rglob("DSC_*"))
    assert left == ["DSC_0003.NEF"]                        # seule la photo non copiée reste
    assert not list(tmp.rglob("*.part"))


def test_run_keeps_going_when_full_and_resumes(env):
    db, sim, card, tmp = env
    sim.shoot()
    sim.shoot()
    disks = FakeDisks(interne=1600, sd=0)
    sp = pool(tmp, disks)
    w = Watcher(db, DirectoryLister(card), Downloader(db, DirectorySource(card), sp), stable_polls=1)
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) == 2:
            disks.free["sd"] = 100_000                     # on a libéré de la place

    w.run(interval=0, reconnect_delay=0, full_retry_delay=60, sleep=sleep,
          should_stop=lambda: len(sleeps) >= 2 and db.count_unsaved() == 0)
    assert 60 in sleeps
    assert db.count_unsaved() == 0
    kinds = [e["kind"] for e in db.events()]
    assert kinds.count("storage_full") == 1 and "storage_ok" in kinds


def test_on_disk_full_hook_frees_space(env):
    db, sim, card, tmp = env
    sim.shoot()
    disks = FakeDisks(interne=0, sd=0)
    w = Watcher(db, DirectoryLister(card), Downloader(db, DirectorySource(card), pool(tmp, disks)), stable_polls=1)
    calls = []

    def free_space():
        calls.append(1)
        disks.free["interne"] = 100_000
        return 100_000

    w.run(interval=0, sleep=lambda s: None, on_disk_full=free_space,
          should_stop=lambda: db.count_unsaved() == 0 and len(calls) > 0)
    assert calls == [1] and db.count_unsaved() == 0


def test_session_move_stays_on_same_location(env):
    db, sim, card, tmp = env
    sim.shoot()
    sp = pool(tmp, FakeDisks(interne=0, sd=100_000))       # tout va sur « sd »
    s = db.create_session("Concert", day="2026-11-25")
    Watcher(db, DirectoryLister(card), Downloader(db, DirectorySource(card), sp), stable_polls=1).poll_once()
    pid = db.conn.execute("SELECT id FROM photos").fetchone()[0]
    move_photos(db, sp, [pid], s.id, rename=True)
    assert Path(db.get(pid).local_path) == tmp / "sd" / "2026-11-25_Concert" / "2026-11-25_Concert_0001.NEF"


def test_recover_cleans_parts_in_every_location(env):
    db, sim, card, tmp = env
    (tmp / "sd" / "x").mkdir(parents=True)
    (tmp / "sd" / "x" / ".DSC_0001.NEF.1.part").write_bytes(b"x")
    Downloader(db, DirectorySource(card), pool(tmp, FakeDisks(interne=10 ** 6, sd=10 ** 6))).recover()
    assert not list(tmp.rglob("*.part"))
