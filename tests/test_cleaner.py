"""Effacement automatique de la carte de l'appareil après vérification (règle 2)."""
import os
import time

import pytest

from cadt.camera import DirectoryLister
from cadt.cleaner import CameraCleaner
from cadt.db import Database
from cadt.downloader import DirectorySource, Downloader, SourceBusy
from cadt.simulator import SimulatedCamera
from cadt.watcher import Watcher


@pytest.fixture
def env(tmp_path):
    card = tmp_path / "card"
    db = Database(tmp_path / "cadt.db")
    sim = SimulatedCamera(card, raw_size=5000, slots=2)
    src = DirectorySource(card)
    w = Watcher(db, DirectoryLister(card), Downloader(db, src, tmp_path / "sd"), stable_polls=1,
                cleaner=CameraCleaner(db, src))
    yield db, sim, w, card
    db.close()


def on_card(card):
    return sorted(str(p.relative_to(card)) for p in card.rglob("DSC_*"))


def test_verified_photos_are_deleted_from_camera(env):
    db, sim, w, card = env
    sim.shoot()
    sim.shoot()
    r = w.poll_once()
    assert r.saved == 2 and r.deleted_on_camera == 2
    assert on_card(card) == []
    for p in db.conn.execute("SELECT * FROM photos"):
        assert p["state"] == "verified" and p["delete_state"] == "deleted_on_camera"
        assert os.path.exists(p["local_path"])  # la copie est bien là


def test_unverified_photo_is_never_deleted(env, monkeypatch):
    db, sim, w, card = env
    sim.shoot()
    import cadt.downloader as dl
    monkeypatch.setattr(dl, "sha256_file", lambda path, drop_cache=True: "0" * 64)  # copie fausse
    r = w.poll_once()
    assert r.saved == 0 and r.deleted_on_camera == 0
    assert on_card(card) == ["store_00010001/DCIM/100NC850/DSC_0001.NEF"]


def test_backup_mode_deletes_both_copies(env):
    db, sim, w, card = env
    sim.slot2_mode = "backup"
    sim.shoot()
    assert len(on_card(card)) == 2
    r = w.poll_once()
    assert r.saved == 1 and r.deleted_on_camera == 1
    assert on_card(card) == []


def test_reused_name_is_not_deleted(env):
    """Une autre photo porte maintenant le même nom (compteur reparti à 0001) : on n'y touche pas."""
    db, sim, w, card = env
    sim.shoot()
    db.set_auto_delete(False)
    w.poll_once()                                         # copiée, pas effacée
    f = card / "store_00010001/DCIM/100NC850/DSC_0001.NEF"
    f.write_bytes(os.urandom(7000))                        # nouvelle photo, même nom
    db.set_auto_delete(True)
    db.conn.execute("UPDATE app_state SET value = '' WHERE key = 'auto_delete_since'")
    w.cleaner.run(DirectoryLister(card).list_files())
    assert f.exists()
    first = db.conn.execute("SELECT * FROM photos ORDER BY id LIMIT 1").fetchone()
    assert first["delete_state"] == "deleted_on_camera"   # l'original n'y est plus
    assert "déjà absente" in db.events(first["id"])[-1]["message"]


def test_disabled_session_photos_stay_after_reenabling(env):
    db, sim, w, card = env
    db.set_auto_delete(False)
    sim.shoot()
    w.poll_once()
    assert len(on_card(card)) == 1
    time.sleep(0.01)
    db.set_auto_delete(True)
    sim.shoot()
    w.poll_once()
    w.poll_once()
    assert on_card(card) == ["store_00010001/DCIM/100NC850/DSC_0001.NEF"]  # la 1re reste


def test_crash_after_camera_delete_is_completed(env):
    db, sim, w, card = env
    db.set_auto_delete(False)
    sim.shoot()
    w.poll_once()
    db.set_auto_delete(True)
    db.conn.execute("UPDATE app_state SET value = '' WHERE key = 'auto_delete_since'")
    p = db.conn.execute("SELECT id FROM photos").fetchone()["id"]
    db.request_delete(p)
    (card / "store_00010001/DCIM/100NC850/DSC_0001.NEF").unlink()   # effacé, puis coupure
    r = w.cleaner.run([])
    assert r.already_absent == [p]
    assert db.get(p).delete_state == "deleted_on_camera"


def test_busy_camera_stops_quietly(env):
    db, sim, w, card = env
    db.set_auto_delete(False)
    sim.shoot()
    w.poll_once()
    db.set_auto_delete(True)
    db.conn.execute("UPDATE app_state SET value = '' WHERE key = 'auto_delete_since'")

    class Busy(DirectorySource):
        def delete(self, folder, name):
            raise SourceBusy("occupé")

    r = CameraCleaner(db, Busy(card)).run(DirectoryLister(card).list_files())
    assert r.deleted == [] and r.errors == []
    p = db.conn.execute("SELECT * FROM photos").fetchone()
    assert p["delete_state"] == "delete_requested" and len(on_card(card)) == 1
    assert CameraCleaner(db, DirectorySource(card)).run(DirectoryLister(card).list_files()).deleted


def test_delete_through_gphoto2(tmp_path):
    pytest.importorskip("gphoto2")
    from cadt.gphoto_source import Gphoto2Source
    card = tmp_path / "card"
    sim = SimulatedCamera(card, raw_size=5000, slots=2, slot2_mode="backup")
    sim.shoot()
    source = Gphoto2Source.directory(str(card))
    with Database(tmp_path / "c.db") as db, source:
        w = Watcher(db, source, Downloader(db, source, tmp_path / "sd"), stable_polls=1,
                    cleaner=CameraCleaner(db, source))
        r = w.poll_once()
        assert r.saved == 1 and r.deleted_on_camera == 1
    assert on_card(card) == []
