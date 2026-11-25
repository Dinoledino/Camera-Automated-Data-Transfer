import os
from datetime import datetime, timedelta, timezone

import pytest

import cadt.downloader as dl
from cadt.db import Database
from cadt.downloader import DirectorySource, Downloader, RetryPolicy, SourceDisconnected

FOLDER = "/store_00010001/DCIM/100NC850"
T0 = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def env(tmp_path):
    cam = tmp_path / "camera"
    (cam / FOLDER.lstrip("/")).mkdir(parents=True)
    db = Database(tmp_path / "cadt.db")
    clock = Clock()
    d = Downloader(db, DirectorySource(cam), tmp_path / "sd", clock=clock)
    yield cam, db, d, clock
    db.close()


def shoot(cam, db, name, t="2026-11-25T12:00:00", size=5000):
    (cam / FOLDER.lstrip("/") / name).write_bytes(os.urandom(size))
    return db.add_detected(FOLDER, name, size, t)[0]


def vanish(cam, name):
    (cam / FOLDER.lstrip("/") / name).unlink()


# ------------------------------------------------------------------ ordre

def test_order_priority_then_jpeg_then_chronological(env):
    cam, db, d, _ = env
    raw_old = shoot(cam, db, "DSC_0001.NEF", t="2026-11-25T10:00:00")
    jpg_new = shoot(cam, db, "DSC_0003.JPG", t="2026-11-25T10:00:09")
    jpg_old = shoot(cam, db, "DSC_0002.JPG", t="2026-11-25T10:00:05")
    raw_new = shoot(cam, db, "DSC_0004.NEF", t="2026-11-25T10:00:10")
    db.set_priority([raw_new.id])

    order = [o.photo.id for o in d.drain()]
    assert order == [raw_new.id, jpg_old.id, jpg_new.id, raw_old.id]


# ------------------------------------------------------------------ délais

def test_failed_photo_waits_and_does_not_block_others(env):
    cam, db, d, clock = env
    bad = shoot(cam, db, "DSC_0001.JPG", t="2026-11-25T10:00:00")
    good = shoot(cam, db, "DSC_0002.JPG", t="2026-11-25T10:00:01")
    vanish(cam, "DSC_0001.JPG")

    outs = d.drain()

    assert [o.photo.id for o in outs] == [bad.id, good.id]  # une seule tentative, puis la suivante
    assert db.get(good.id).state == "verified"
    assert db.get(bad.id).next_attempt_at is not None
    assert d.seconds_until_next_retry() == pytest.approx(5)
    assert d.drain() == []  # délai pas écoulé

    clock.advance(5)
    assert [o.photo.id for o in d.drain()] == [bad.id]


def test_backoff_sequence_then_give_up_then_manual_retry(env):
    cam, db, d, clock = env
    photo = shoot(cam, db, "DSC_0001.JPG")
    vanish(cam, "DSC_0001.JPG")

    delays = []
    for _ in range(4):
        d.drain()
        delays.append(d.seconds_until_next_retry())
        clock.advance(delays[-1])
    assert delays == pytest.approx([5, 15, 45, 135])

    d.drain()  # 5e échec : abandon
    p = db.get(photo.id)
    assert p.given_up_at is not None and p.state == "detected"
    assert d.seconds_until_next_retry() is None
    assert db.count_given_up() == 1
    assert db.count_unsaved() == 1  # jamais cachée : toujours « pas sauvegardée »
    clock.advance(3600)
    assert d.drain() == []  # plus jamais reprise automatiquement

    shoot(cam, db, "DSC_0001.JPG", size=5000)  # le fichier redevient lisible
    assert db.retry([photo.id]) == 1
    [out] = d.drain()
    assert out.ok and db.count_unsaved() == 0


def test_max_delay_is_capped():
    p = RetryPolicy(max_attempts=50)
    assert p.delay(1) == 5 and p.delay(20) == 600


def test_hash_mismatch_also_waits(env, monkeypatch):
    cam, db, d, _ = env
    photo = shoot(cam, db, "DSC_0001.NEF")
    monkeypatch.setattr(dl, "sha256_file", lambda path, drop_cache=True: "0" * 64)
    [out] = d.drain()  # pas de boucle infinie : une seule tentative
    assert not out.ok
    assert db.get(photo.id).next_attempt_at is not None


# ------------------------------------------------------------------ sans pénalité

def test_disconnect_is_not_counted_and_has_no_delay(env):
    cam, db, d, _ = env
    photo = shoot(cam, db, "DSC_0001.NEF")

    class Unplugged(DirectorySource):
        def iter_chunks(self, folder, name):
            raise SourceDisconnected("câble débranché")
            yield b""

    d.source = Unplugged(cam)
    for _ in range(10):
        with pytest.raises(SourceDisconnected):
            d.drain()
    p = db.get(photo.id)
    assert p.attempts == 0 and p.next_attempt_at is None and p.given_up_at is None

    d.source = DirectorySource(cam)
    [out] = d.drain()  # rebranché : reprise immédiate
    assert out.ok


def test_crash_counts_as_attempt(env):
    """Un fichier qui fait planter le programme à chaque fois finira abandonné."""
    cam, db, d, _ = env
    photo = shoot(cam, db, "DSC_0001.NEF")
    db.claim_next()
    d.recover()  # redémarrage après plantage
    assert db.get(photo.id).attempts == 1


def test_priority_skips_wait(env):
    cam, db, d, _ = env
    photo = shoot(cam, db, "DSC_0001.JPG")
    vanish(cam, "DSC_0001.JPG")
    d.drain()
    shoot(cam, db, "DSC_0001.JPG")
    db.set_priority([photo.id])
    [out] = d.drain()
    assert out.ok
