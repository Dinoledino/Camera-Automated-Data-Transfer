"""Corbeille : rejeter = supprimer en trois temps, récupérable jusqu'à l'extinction ou au délai."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cadt.camera import DirectoryLister
from cadt.cleaner import CameraCleaner
from cadt.db import Database, InvalidTransition
from cadt.downloader import DirectorySource, Downloader
from cadt.sessions import move_photos
from cadt.simulator import SimulatedCamera
from cadt.trash import TRASH_DIR, Trash
from cadt.watcher import Watcher

T0 = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


class Env:
    def __init__(self, tmp):
        self.tmp = tmp
        self.card = tmp / "card"
        self.sd = tmp / "sd"
        self.db = Database(tmp / "cadt.db")
        self.sim = SimulatedCamera(self.card, raw_size=4000, slots=2)
        self.boot = "boot-A"
        self.clock = T0
        self.trash = Trash(self.db, self.sd, boot_id=lambda: self.boot, now=lambda: self.clock)
        src = DirectorySource(self.card)
        self.watcher = Watcher(self.db, DirectoryLister(self.card), Downloader(self.db, src, self.sd),
                               stable_polls=1, cleaner=CameraCleaner(self.db, src), trash=self.trash)

    def photos(self, n):
        for _ in range(n):
            self.sim.shoot()
        self.watcher.poll_once()
        return [r["id"] for r in self.db.conn.execute("SELECT id FROM photos ORDER BY id")]


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.db.close()


def test_put_and_restore(env):
    a, b = env.photos(2)
    original = Path(env.db.get(a).local_path)
    data = original.read_bytes()
    env.db.set_flag(a, "reject")

    env.trash.put([a])
    p = env.db.get(a)
    assert p.in_trash and not original.exists()
    assert f"/{TRASH_DIR.as_posix()}/" in p.local_path and Path(p.local_path).read_bytes() == data
    assert [x.id for x in env.db.trashed_photos()] == [a]

    env.trash.restore([a])
    p = env.db.get(a)
    assert not p.in_trash and Path(p.local_path) == original and original.read_bytes() == data
    assert p.flag == "none"                    # récupérée = plus rejetée


def test_expires_after_delay(env):
    (a,) = env.photos(1)
    env.trash.put([a])
    path = Path(env.db.get(a).local_path)
    env.clock = T0 + timedelta(hours=23)
    assert env.trash.expire() == 0
    assert env.trash.seconds_left(env.db.get(a)) == pytest.approx(3600)
    env.clock = T0 + timedelta(hours=24, seconds=1)
    assert env.trash.expire() == 1
    assert env.db.get(a).purged_at is not None and not path.exists()


def test_configurable_delay(env):
    (a,) = env.photos(1)
    env.trash.set_delay_hours(1)
    env.trash.put([a])
    env.clock = T0 + timedelta(minutes=61)
    assert env.trash.expire() == 1


def test_phone_reboot_empties_trash_but_app_restart_does_not(env):
    a, b = env.photos(2)
    env.trash.put([a, b])
    assert env.trash.expire() == 0             # même démarrage (application relancée)
    env.boot = "boot-B"                        # le téléphone a redémarré
    assert env.trash.expire() == 2


def test_full_phone_empties_trash_oldest_first(env):
    a, b, c = env.photos(3)
    env.trash.put([a])
    env.clock = T0 + timedelta(minutes=5)
    env.trash.put([b])
    freed = env.trash.free_space(target_bytes=1)
    assert freed == 4000
    assert env.db.get(a).purged_at and not env.db.get(b).purged_at


def test_purge_removes_thumbnails(env, monkeypatch):
    (a,) = env.photos(1)
    thumb = env.sd / ".cadt" / "thumbs" / f"{a}.jpg"
    thumb.parent.mkdir(parents=True)
    thumb.write_bytes(b"jpg")
    env.db.conn.execute("INSERT INTO media (photo_id, thumb_path, indexed_at) VALUES (?, ?, 'x')", (a, str(thumb)))
    env.trash.put([a])
    env.trash.purge([a])
    assert not thumb.exists()


def test_purged_photo_is_never_deleted_from_camera(env):
    env.db.set_auto_delete(False)
    (a,) = env.photos(1)                        # copiée, laissée sur l'appareil
    env.trash.put([a])
    env.trash.purge([a])
    env.db.set_auto_delete(True)
    env.db.conn.execute("UPDATE app_state SET value = '' WHERE key = 'auto_delete_since'")
    env.watcher.cleaner.run(DirectoryLister(env.card).list_files())
    assert list(env.card.rglob("DSC_*"))        # la dernière copie reste sur l'appareil


def test_trashed_photo_cannot_be_moved_and_purge_is_final(env):
    (a,) = env.photos(1)
    s = env.db.create_session("X", day="2026-11-25")
    env.trash.put([a])
    assert move_photos(env.db, env.sd, [a], s.id).refused
    env.trash.purge([a])
    with pytest.raises(InvalidTransition):
        env.db.mark_restored(a)
    assert env.trash.restore([a]) == []


def test_recover_after_crash_between_move_and_db(env):
    (a,) = env.photos(1)
    p = env.db.get(a)
    src = Path(p.local_path)
    dst = env.trash.trash_dir(p.local_path) / f"{a}_{src.name}"
    env.db.begin_move(a, str(dst), p.session_id, p.session_number)
    dst.parent.mkdir(parents=True)
    os.replace(src, dst)                       # coupure ici
    env.watcher.downloader.recover()           # termine le déplacement
    env.trash.recover()                        # constate la mise à la corbeille
    p = env.db.get(a)
    assert p.in_trash and Path(p.trash_origin) == src
    env.trash.restore([a])
    assert src.exists()


def test_recover_marks_missing_trash_file_purged(env):
    (a,) = env.photos(1)
    env.trash.put([a])
    Path(env.db.get(a).local_path).unlink()
    env.trash.recover()
    assert env.db.get(a).purged_at is not None


def test_watcher_uses_trash_when_full(env, monkeypatch):
    (a,) = env.photos(1)
    env.trash.put([a])
    env.sim.shoot()
    calls = []
    real = env.trash.free_space
    monkeypatch.setattr(env.trash, "free_space", lambda *a, **k: calls.append(1) or real(*a, **k))
    full = {"on": True}
    real_choose = env.watcher.downloader.pool.choose
    monkeypatch.setattr(env.watcher.downloader.pool, "choose",
                        lambda size: None if full["on"] and not calls else real_choose(size))
    env.watcher.run(interval=0, sleep=lambda s: None,
                    should_stop=lambda: env.db.count_unsaved() == 0 and bool(calls))
    assert calls and env.db.get(a).purged_at is not None
