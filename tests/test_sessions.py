import os
from pathlib import Path

import pytest

from cadt.db import Database, slugify
from cadt.downloader import DirectorySource, Downloader
from cadt.sessions import move_photos, recover_moves

FOLDER = "/store_00010001/DCIM/100NC850"
T = "2026-11-23T10:00:00"


@pytest.fixture
def env(tmp_path):
    cam = tmp_path / "camera"
    (cam / FOLDER.lstrip("/")).mkdir(parents=True)
    sd = tmp_path / "sd"
    db = Database(tmp_path / "cadt.db")
    yield cam, sd, db, Downloader(db, DirectorySource(cam), sd)
    db.close()


def shoot(cam, db, name, t=T, size=10_000):
    data = os.urandom(size)
    (cam / FOLDER.lstrip("/") / name).write_bytes(data)
    photo, _ = db.add_detected(FOLDER, name, size, t)
    return photo, data


def path_of(db, photo):
    return Path(db.get(photo.id).local_path)


# ------------------------------------------------------------------ création

def test_slugify():
    assert slugify("Mariage Dupont") == "Mariage-Dupont"
    assert slugify("  Rando : Vercors (été) ") == "Rando-Vercors-ete"
    assert slugify("???") == "session"


def test_same_name_same_day_gets_distinct_folder(env):
    _, _, db, _ = env
    a = db.create_session("Concert", day="2026-11-23")
    b = db.create_session("Concert", day="2026-11-23")
    assert a.folder == "2026-11-23_Concert"
    assert b.folder == "2026-11-23_Concert-2"


def test_empty_name_refused(env):
    _, _, db, _ = env
    with pytest.raises(ValueError):
        db.create_session("   ")


def test_migration_from_v1(tmp_path):
    """Une base créée en version 1 passe en version 2 sans perdre ses photos."""
    import sqlite3
    from cadt.db import _SCHEMA_FILE, _split_sql
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    for st in _split_sql(_SCHEMA_FILE.read_text()):
        c.execute(st)
    c.execute("INSERT INTO photos (camera_folder, name, size, capture_time, kind, group_key, "
              "detected_at, updated_at) VALUES ('/d', 'DSC_0001.NEF', 1, ?, 'raw', 'k', 'x', 'x')", (T,))
    c.execute("PRAGMA user_version = 1")
    c.commit()
    c.close()
    with Database(path) as db:
        p = db.find("DSC_0001.NEF", 1, T)
        assert p is not None and p.session_id is None
        db.create_session("Test")


# ------------------------------------------------------------------ session active

def test_active_session_names_files(env):
    cam, sd, db, d = env
    s = db.create_session("Mariage Dupont", day="2026-11-23")
    db.set_active_session(s.id)
    raw1, _ = shoot(cam, db, "DSC_0412.NEF", t="2026-11-23T10:00:00")
    jpg1, _ = shoot(cam, db, "DSC_0412.JPG", t="2026-11-23T10:00:00")
    raw2, _ = shoot(cam, db, "DSC_0413.NEF", t="2026-11-23T10:00:02")
    d.drain()

    folder = sd / "2026-11-23_Mariage-Dupont"
    assert path_of(db, raw1) == folder / "2026-11-23_Mariage-Dupont_0001.NEF"
    assert path_of(db, jpg1) == folder / "2026-11-23_Mariage-Dupont_0001.JPG"  # même prise, même numéro
    assert path_of(db, raw2) == folder / "2026-11-23_Mariage-Dupont_0002.NEF"


def test_no_active_session_uses_dated_folder(env):
    cam, sd, db, d = env
    s = db.create_session("Matin", day="2026-11-23")
    db.set_active_session(s.id)
    db.set_active_session(None)
    photo, _ = shoot(cam, db, "DSC_0001.NEF")
    d.drain()
    assert path_of(db, photo) == sd / "2026-11-23" / "DSC_0001.NEF"
    assert db.get(photo.id).session_id is None


def test_session_date_not_photo_date(env):
    """Une soirée qui passe minuit garde un seul préfixe."""
    cam, sd, db, d = env
    s = db.create_session("Nouvel an", day="2026-12-31")
    db.set_active_session(s.id)
    photo, _ = shoot(cam, db, "DSC_0001.NEF", t="2027-01-01T00:30:00")
    d.drain()
    assert path_of(db, photo).name == "2026-12-31_Nouvel-an_0001.NEF"


def test_numbering_continues_across_restarts(env, tmp_path):
    cam, sd, db, d = env
    s = db.create_session("Rando", day="2026-11-23")
    db.set_active_session(s.id)
    shoot(cam, db, "DSC_0001.NEF")
    db.close()
    with Database(tmp_path / "cadt.db") as db2:
        assert db2.active_session().id == s.id
        p, _ = db2.add_detected(FOLDER, "DSC_0002.NEF", 5, "2026-11-23T11:00:00")
        assert p.session_number == 2


# ------------------------------------------------------------------ déplacements

def _two_sessions_with_photo(env):
    cam, sd, db, d = env
    a = db.create_session("Ceremonie", day="2026-11-23")
    b = db.create_session("Cocktail", day="2026-11-23")
    db.set_active_session(a.id)
    photo, data = shoot(cam, db, "DSC_0001.NEF")
    d.drain()
    return a, b, photo, data


def test_move_keeps_name_by_default(env):
    cam, sd, db, d = env
    a, b, photo, data = _two_sessions_with_photo(env)
    old = path_of(db, photo)

    res = move_photos(db, sd, [photo.id], b.id)

    assert len(res.moved) == 1
    new = path_of(db, photo)
    assert new == sd / b.folder / "2026-11-23_Ceremonie_0001.NEF"  # nom conservé
    assert new.read_bytes() == data and not old.exists()
    p = db.get(photo.id)
    assert p.session_id == b.id and p.state == "verified"


def test_move_with_rename(env):
    cam, sd, db, d = env
    a, b, photo, data = _two_sessions_with_photo(env)
    move_photos(db, sd, [photo.id], b.id, rename=True)
    assert path_of(db, photo) == sd / b.folder / "2026-11-23_Cocktail_0001.NEF"
    assert path_of(db, photo).read_bytes() == data


def test_move_back_to_dated_folder(env):
    cam, sd, db, d = env
    a, b, photo, data = _two_sessions_with_photo(env)
    move_photos(db, sd, [photo.id], None, rename=True)
    assert path_of(db, photo) == sd / "2026-11-23" / "DSC_0001.NEF"
    assert db.get(photo.id).session_id is None


def test_move_never_overwrites_another_photo(env):
    """Deux DSC_0001.NEF différents (compteur reparti à zéro) rangés sans renommage dans B."""
    cam, sd, db, d = env
    b = db.create_session("B", day="2026-11-24")
    x, data_x = shoot(cam, db, "DSC_0001.NEF", t="2026-11-23T10:00:00")
    d.drain()
    (cam / FOLDER.lstrip("/") / "DSC_0001.NEF").unlink()
    y, data_y = shoot(cam, db, "DSC_0001.NEF", t="2026-11-24T09:00:00")
    d.drain()

    move_photos(db, sd, [x.id], b.id)
    move_photos(db, sd, [y.id], b.id)

    assert path_of(db, x) == sd / b.folder / "DSC_0001.NEF"
    assert path_of(db, y) == sd / b.folder / f"DSC_0001_{y.id}.NEF"
    assert path_of(db, x).read_bytes() == data_x
    assert path_of(db, y).read_bytes() == data_y


def test_detected_photo_is_reassigned_without_file(env):
    cam, sd, db, d = env
    a = db.create_session("A", day="2026-11-23")
    b = db.create_session("B", day="2026-11-23")
    db.set_active_session(a.id)
    photo, _ = shoot(cam, db, "DSC_0001.NEF")

    res = move_photos(db, sd, [photo.id], b.id)
    assert [p.id for p in res.reassigned] == [photo.id]
    d.drain()
    assert path_of(db, photo) == sd / b.folder / f"{b.folder}_0001.NEF"


def test_downloading_photo_cannot_move(env):
    cam, sd, db, d = env
    b = db.create_session("B", day="2026-11-23")
    photo, _ = shoot(cam, db, "DSC_0001.NEF")
    db.claim_next()
    res = move_photos(db, sd, [photo.id], b.id)
    assert res.refused and res.refused[0][0] == photo.id


# ------------------------------------------------------------------ coupures

def test_crash_after_intent_before_rename(env):
    cam, sd, db, d = env
    a, b, photo, data = _two_sessions_with_photo(env)
    src = path_of(db, photo)
    db.begin_move(photo.id, str(sd / b.folder / src.name), b.id, 1)  # puis coupure

    assert recover_moves(db) == []
    p = db.get(photo.id)
    assert Path(p.local_path) == src and p.session_id == a.id
    assert db.pending_moves() == []


def test_crash_after_rename_before_db_update(env):
    cam, sd, db, d = env
    a, b, photo, data = _two_sessions_with_photo(env)
    src = path_of(db, photo)
    dst = sd / b.folder / src.name
    db.begin_move(photo.id, str(dst), b.id, 1)
    dst.parent.mkdir(parents=True)
    os.replace(src, dst)  # puis coupure

    d.recover()  # le démarrage normal termine le déplacement
    p = db.get(photo.id)
    assert Path(p.local_path) == dst and p.session_id == b.id
    assert dst.read_bytes() == data


def test_file_missing_everywhere_is_reported(env):
    cam, sd, db, d = env
    a, b, photo, data = _two_sessions_with_photo(env)
    src = path_of(db, photo)
    db.begin_move(photo.id, str(sd / b.folder / src.name), b.id, 1)
    src.unlink()
    assert recover_moves(db) == [photo.id]
    assert db.get(photo.id).state == "verified"  # on ne touche à rien : à l'humain de voir
