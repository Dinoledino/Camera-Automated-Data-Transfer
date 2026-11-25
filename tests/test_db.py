import sqlite3
import subprocess
import sys
import textwrap

import pytest

from cadt.db import (
    Database,
    HashMismatch,
    SCHEMA_VERSION,
    InvalidTransition,
    classify_kind,
    make_group_key,
)

H1 = "a" * 64
H2 = "b" * 64
T = "2026-11-23T10:00:00"


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "cadt.db")
    yield d
    d.close()


def add(db, name="DSC_0001.NEF", size=50_000_000, t=T):
    photo, _ = db.add_detected("/store_00010001/DCIM/100NC850", name, size, t)
    return photo


def verify(db, photo_id, sha=H1, path="/sd/x"):
    db.mark_downloaded(photo_id, sha)
    return db.mark_verified(photo_id, path, sha)


# --------------------------------------------------------------------- base

def test_pragmas_and_version(db):
    assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_reopen_keeps_data(tmp_path):
    with Database(tmp_path / "x.db") as d:
        add(d)
    with Database(tmp_path / "x.db") as d:
        assert d.count_unsaved() == 1


def test_classify_and_group():
    assert classify_kind("DSC_0001.NEF") == "raw"
    assert classify_kind("dsc_0001.jpg") == "jpeg"
    assert classify_kind("DSC_0001.MOV") == "other"
    assert make_group_key("DSC_0001.NEF", T) == make_group_key("dsc_0001.JPG", T)


# --------------------------------------------------------------- détection

def test_add_detected_is_idempotent(db):
    p1, created1 = db.add_detected("/a", "DSC_0001.NEF", 10, T)
    p2, created2 = db.add_detected("/b", "DSC_0001.NEF", 10, T)
    assert created1 and not created2
    assert p1.id == p2.id
    assert len(db.events(p1.id)) == 1


def test_counter_rollover_is_a_new_photo(db):
    """DSC_0001 après 9999 : même nom, autre date → autre photo."""
    p1 = add(db, t="2026-01-01T10:00:00")
    p2 = add(db, t="2026-06-01T10:00:00")
    assert p1.id != p2.id


# ------------------------------------------------------------------ file

def test_queue_priority_jpeg_first_then_chronological(db):
    raw_early = add(db, "DSC_0001.NEF", t="2026-11-23T10:00:00")
    jpeg_late = add(db, "DSC_0002.JPG", size=5, t="2026-11-23T10:00:05")
    jpeg_early = add(db, "DSC_0001.JPG", size=5, t="2026-11-23T10:00:00")
    order = [db.claim_next().id for _ in range(3)]
    assert order == [jpeg_early.id, jpeg_late.id, raw_early.id]
    assert db.claim_next() is None


def test_claim_increments_attempts(db):
    p = add(db)
    assert db.claim_next().attempts == 1
    db.mark_failed(p.id, "usb unplugged")
    assert db.claim_next().attempts == 2


def test_claim_is_exclusive_across_connections(tmp_path):
    path = tmp_path / "x.db"
    with Database(path) as a, Database(path) as b:
        add(a)
        assert a.claim_next() is not None
        assert b.claim_next() is None


# --------------------------------------------------------------- sauvegarde

def test_happy_path(db):
    p = add(db)
    db.claim_next()
    db.mark_downloaded(p.id, H1.upper())
    v = db.mark_verified(p.id, "/sd/DSC_0001.NEF", H1)
    assert v.state == "verified" and v.sha256 == H1 and v.verified_at
    assert db.count_unsaved() == 0
    s = db.mark_synced(p.id, H1)
    assert s.state == "synced_pc"


def test_hash_mismatch_on_reread_requeues(db):
    p = add(db)
    db.claim_next()
    db.mark_downloaded(p.id, H1)
    with pytest.raises(HashMismatch):
        db.mark_verified(p.id, "/sd/x", H2)
    got = db.get(p.id)
    assert got.state == "detected" and got.sha256 is None
    assert got.last_error == "hash mismatch on reread"
    assert any(e["kind"] == "hash_mismatch" for e in db.events(p.id))
    assert db.count_unsaved() == 1


def test_cannot_verify_without_download(db):
    p = add(db)
    with pytest.raises(InvalidTransition):
        db.mark_verified(p.id, "/sd/x", H1)
    db.claim_next()
    with pytest.raises(InvalidTransition):
        db.mark_verified(p.id, "/sd/x", H1)


def test_cannot_download_unclaimed(db):
    p = add(db)
    with pytest.raises(InvalidTransition):
        db.mark_downloaded(p.id, H1)


def test_sync_with_different_hash_is_refused(db):
    p = add(db)
    db.claim_next()
    verify(db, p.id)
    with pytest.raises(HashMismatch):
        db.mark_synced(p.id, H2)
    assert db.get(p.id).state == "verified"
    assert any(e["kind"] == "sync_hash_mismatch" for e in db.events(p.id))


def test_failed_download_cannot_touch_verified(db):
    p = add(db)
    db.claim_next()
    verify(db, p.id)
    with pytest.raises(InvalidTransition):
        db.mark_failed(p.id, "boom")
    assert db.get(p.id).state == "verified"


# ------------------------------------------------------------------ reprise

def test_reset_interrupted(db):
    a, b, c = add(db, "A.JPG"), add(db, "B.JPG"), add(db, "C.JPG")
    db.claim_next()                     # A : downloading
    db.claim_next()                     # B : downloading
    db.mark_downloaded(b.id, H1)        # B : downloaded
    reset = db.reset_interrupted()
    assert {p.id for p in reset} == {a.id, b.id}
    for pid in (a.id, b.id, c.id):
        assert db.get(pid).state == "detected"
    assert db.get(b.id).sha256 is None


def test_reset_does_not_touch_verified(db):
    p = add(db)
    db.claim_next()
    verify(db, p.id)
    assert db.reset_interrupted() == []
    assert db.get(p.id).state == "verified"


def test_kill_during_transaction_rolls_back(tmp_path):
    """Un processus tué en pleine transaction ne laisse aucune trace partielle."""
    path = tmp_path / "x.db"
    with Database(path) as d:
        add(d)
    script = textwrap.dedent(f"""
        import os
        from cadt.db import Database
        d = Database({str(path)!r})
        d.conn.execute("BEGIN IMMEDIATE")
        d.conn.execute("UPDATE photos SET state='downloading'")
        os._exit(9)   # mort brutale, sans COMMIT
    """)
    subprocess.run([sys.executable, "-c", script], check=False)
    with Database(path) as d:
        assert d.get(1).state == "detected"


# ------------------------------------------------------------- garde-fous SQL

def test_trigger_verified_requires_hash(db):
    p = add(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("UPDATE photos SET state='verified', local_path='/x' WHERE id=?", (p.id,))


def test_trigger_hash_immutable(db):
    p = add(db)
    db.claim_next()
    verify(db, p.id)
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("UPDATE photos SET sha256=? WHERE id=?", (H2, p.id))


def test_trigger_no_downgrade(db):
    p = add(db)
    db.claim_next()
    verify(db, p.id)
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("UPDATE photos SET state='detected' WHERE id=?", (p.id,))


def test_bad_sha_format_rejected(db):
    p = add(db)
    db.claim_next()
    with pytest.raises(sqlite3.IntegrityError):
        db.mark_downloaded(p.id, "pas-un-hash")


# ---------------------------------------------------------------- suppression

def test_delete_refused_if_not_verified(db):
    p = add(db)
    with pytest.raises(InvalidTransition):
        db.request_delete(p.id)
    with pytest.raises(sqlite3.IntegrityError):  # même en contournant le code Python
        db.conn.execute("UPDATE photos SET delete_state='delete_requested' WHERE id=?", (p.id,))
    assert db.get(p.id).delete_state == "none"


def test_delete_two_steps(db):
    p = add(db)
    db.claim_next()
    verify(db, p.id)
    with pytest.raises(InvalidTransition):
        db.mark_deleted_on_camera(p.id)       # pas de suppression sans demande préalable
    db.request_delete(p.id)
    db.cancel_delete(p.id)
    assert db.get(p.id).delete_state == "none"
    db.request_delete(p.id)
    db.mark_deleted_on_camera(p.id)
    assert db.get(p.id).delete_state == "deleted_on_camera"


def test_trigger_delete_order(db):
    p = add(db)
    db.claim_next()
    verify(db, p.id)
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("UPDATE photos SET delete_state='deleted_on_camera' WHERE id=?", (p.id,))


# ------------------------------------------------------------------------ tri

def test_rating_and_flag(db):
    p = add(db)
    db.set_rating(p.id, 4)
    db.set_flag(p.id, "keep")
    got = db.get(p.id)
    assert (got.rating, got.flag) == (4, "keep")
    with pytest.raises(sqlite3.IntegrityError):
        db.set_rating(p.id, 6)
    with pytest.raises(sqlite3.IntegrityError):
        db.set_flag(p.id, "maybe")


def test_creates_missing_parent_folder(tmp_path):
    with Database(tmp_path / "pas" / "encore" / "cadt.db") as d:
        assert d.count_unsaved() == 0
