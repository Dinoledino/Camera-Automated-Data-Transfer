import errno
import hashlib
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import cadt.downloader as dl
from cadt.db import Database
from cadt.downloader import (
    DirectorySource,
    DiskFull,
    Downloader,
    SourceDisconnected,
    SourceError,
)

FOLDER = "/store_00010001/DCIM/100NC850"
T = "2026-11-23T10:00:00"
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path):
    cam = tmp_path / "camera"
    (cam / FOLDER.lstrip("/")).mkdir(parents=True)
    sd = tmp_path / "sd"
    db = Database(tmp_path / "cadt.db")
    yield cam, sd, db
    db.close()


def shoot(cam, db, name, size=300_000, t=T, seed=None):
    """Crée un fichier sur le « faux appareil » et l'enregistre comme détecté."""
    data = os.urandom(size) if seed is None else bytes([seed]) * size
    (cam / FOLDER.lstrip("/") / name).write_bytes(data)
    photo, _ = db.add_detected(FOLDER, name, size, t)
    return photo, data


def parts(sd):
    return list(sd.rglob("*.part")) if sd.exists() else []


# ------------------------------------------------------------------ cas nominal

def test_download_verifies_and_renames(env):
    cam, sd, db = env
    photo, data = shoot(cam, db, "DSC_0001.NEF")
    d = Downloader(db, DirectorySource(cam, chunk_size=65536), sd)

    [out] = d.drain()

    assert out.ok
    p = db.get(photo.id)
    assert p.state == "verified"
    assert p.sha256 == hashlib.sha256(data).hexdigest()
    assert Path(p.local_path) == sd / "2026-11-23" / "DSC_0001.NEF"
    assert Path(p.local_path).read_bytes() == data
    assert parts(sd) == []
    kinds = [e["kind"] for e in db.events(photo.id)]
    assert kinds == ["detected", "download_started", "downloaded", "verified"]


def test_drain_takes_jpeg_before_raw(env):
    cam, sd, db = env
    raw, _ = shoot(cam, db, "DSC_0001.NEF", t="2026-11-23T10:00:00")
    jpg, _ = shoot(cam, db, "DSC_0002.JPG", t="2026-11-23T10:00:05")
    outs = Downloader(db, DirectorySource(cam), sd).drain()
    assert [o.photo.id for o in outs] == [jpg.id, raw.id]
    assert db.count_unsaved() == 0


def test_same_name_never_overwrites_another_photo(env):
    """Le compteur du D850 repart à DSC_0001 : même nom, même jour, autre photo."""
    cam, sd, db = env
    d = Downloader(db, DirectorySource(cam), sd)
    first, data1 = shoot(cam, db, "DSC_0001.NEF", t="2026-11-23T10:00:00")
    d.drain()
    second, data2 = shoot(cam, db, "DSC_0001.NEF", size=200_000, t="2026-11-23T18:00:00")
    d.drain()

    p1, p2 = db.get(first.id), db.get(second.id)
    assert p1.local_path != p2.local_path
    assert Path(p1.local_path).read_bytes() == data1
    assert Path(p2.local_path).read_bytes() == data2


# ------------------------------------------------------------------ échecs

class FlakySource(DirectorySource):
    """Envoie un morceau, puis lève l'erreur donnée."""

    def __init__(self, root, exc):
        super().__init__(root, chunk_size=4096)
        self.exc = exc

    def iter_chunks(self, folder, name):
        it = super().iter_chunks(folder, name)
        yield next(it)
        raise self.exc


def test_read_error_puts_photo_back_in_queue(env):
    cam, sd, db = env
    photo, _ = shoot(cam, db, "DSC_0001.NEF")
    d = Downloader(db, FlakySource(cam, SourceError("erreur PTP")), sd)

    out = d.download(db.claim_next())

    assert not out.ok
    p = db.get(photo.id)
    assert p.state == "detected" and p.sha256 is None
    assert p.last_error == "erreur PTP"
    assert parts(sd) == []


def test_disconnect_mid_copy_stops_drain(env):
    cam, sd, db = env
    a, _ = shoot(cam, db, "DSC_0001.JPG")
    b, _ = shoot(cam, db, "DSC_0002.JPG", t="2026-11-23T10:00:01")
    d = Downloader(db, FlakySource(cam, SourceDisconnected("câble débranché")), sd)

    with pytest.raises(SourceDisconnected):
        d.drain()

    assert db.get(a.id).state == "detected"
    assert db.get(b.id).attempts == 0  # la suivante n'a pas été tentée
    assert parts(sd) == []
    # Rebranchement : tout repart.
    Downloader(db, DirectorySource(cam), sd).drain()
    assert db.count_unsaved() == 0


def test_truncated_transfer_is_rejected(env):
    cam, sd, db = env
    photo, _ = shoot(cam, db, "DSC_0001.NEF")
    (cam / FOLDER.lstrip("/") / "DSC_0001.NEF").write_bytes(b"x" * 1000)  # moins que size

    out = Downloader(db, DirectorySource(cam), sd).download(db.claim_next())

    assert not out.ok and "taille" in out.error
    assert db.get(photo.id).state == "detected"
    assert not (sd / "2026-11-23" / "DSC_0001.NEF").exists()


def test_hash_mismatch_on_reread(env, monkeypatch):
    """La microSD renvoie autre chose que ce qui a été écrit."""
    cam, sd, db = env
    photo, _ = shoot(cam, db, "DSC_0001.NEF")
    monkeypatch.setattr(dl, "sha256_file", lambda path, drop_cache=True: "0" * 64)

    out = Downloader(db, DirectorySource(cam), sd).download(db.claim_next())

    assert not out.ok
    p = db.get(photo.id)
    assert p.state == "detected" and p.last_error == "hash mismatch on reread"
    assert not (sd / "2026-11-23" / "DSC_0001.NEF").exists()
    assert parts(sd) == []
    assert "hash_mismatch" in [e["kind"] for e in db.events(photo.id)]


def test_disk_full(env, monkeypatch):
    cam, sd, db = env
    photo, _ = shoot(cam, db, "DSC_0001.NEF")
    d = Downloader(db, DirectorySource(cam), sd)

    def full(photo, part):
        part.write_bytes(b"debut")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(d, "_write_part", full)
    with pytest.raises(DiskFull):
        d.drain()

    p = db.get(photo.id)
    assert p.state == "detected" and p.last_error == "microSD pleine"
    assert parts(sd) == []


# ------------------------------------------------------------------ reprise

def test_recover_removes_orphan_part(env):
    cam, sd, db = env
    photo, data = shoot(cam, db, "DSC_0001.NEF")
    d = Downloader(db, DirectorySource(cam), sd)
    claimed = db.claim_next()
    part = d.part_path(claimed)
    part.parent.mkdir(parents=True)
    part.write_bytes(data[:1000])  # coupure en pleine écriture

    assert [p.id for p in d.recover()] == [photo.id]
    assert parts(sd) == []
    d.drain()
    assert Path(db.get(photo.id).local_path).read_bytes() == data


def test_crash_between_rename_and_db_update(env):
    """Le fichier final existe mais la base dit encore « downloaded »."""
    cam, sd, db = env
    photo, data = shoot(cam, db, "DSC_0001.NEF")
    d = Downloader(db, DirectorySource(cam), sd)
    claimed = db.claim_next()
    final = d.final_path(claimed)
    final.parent.mkdir(parents=True)
    final.write_bytes(data)
    db.mark_downloaded(photo.id, hashlib.sha256(data).hexdigest())

    d.recover()
    d.drain()

    p = db.get(photo.id)
    assert p.state == "verified" and Path(p.local_path) == final
    assert final.read_bytes() == data


SLOW_DOWNLOAD = textwrap.dedent("""
    import sys, time
    from cadt.db import Database
    from cadt.downloader import DirectorySource, Downloader

    class Slow(DirectorySource):
        def iter_chunks(self, folder, name):
            for chunk in super().iter_chunks(folder, name):
                time.sleep(0.05)
                yield chunk

    cam, sd, dbpath = sys.argv[1:]
    db = Database(dbpath)
    d = Downloader(db, Slow(cam, chunk_size=65536), sd)
    d.recover()
    d.drain()
""")


def test_kill_9_mid_copy_then_resume(env, tmp_path):
    """Test obligatoire de la phase 3 : appli tuée (kill -9) puis relancée."""
    cam, sd, db = env
    photo, data = shoot(cam, db, "DSC_0001.NEF", size=4 * 1024 * 1024)  # 64 morceaux ≈ 3 s
    dbpath = tmp_path / "cadt.db"

    proc = subprocess.Popen([sys.executable, "-c", SLOW_DOWNLOAD, str(cam), str(sd), str(dbpath)],
                            cwd=REPO)
    deadline = time.monotonic() + 10
    while not parts(sd) or parts(sd)[0].stat().st_size < 512 * 1024:
        assert time.monotonic() < deadline, "le téléchargement n'a pas démarré"
        assert proc.poll() is None, "le processus s'est arrêté trop tôt"
        time.sleep(0.02)
    proc.send_signal(signal.SIGKILL)
    proc.wait()

    assert db.get(photo.id).state == "downloading"
    assert db.count_unsaved() == 1

    d = Downloader(db, DirectorySource(cam), sd)
    d.recover()
    d.drain()

    p = db.get(photo.id)
    assert p.state == "verified"
    assert Path(p.local_path).read_bytes() == data
    assert parts(sd) == []
