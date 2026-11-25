import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from cadt.db import Database
from cadt.downloader import DirectorySource, Downloader, SourceDisconnected
from cadt.simulator import SimulatedCamera
from cadt.watcher import Watcher

FOLDER = "/DCIM/100NC850"


@dataclass(frozen=True)
class F:
    folder: str
    name: str
    size: int
    capture_time: str = "2026-11-25T14:00:00"
    storage_id: str | None = None

    @property
    def key(self):
        return (self.name, self.size, self.capture_time)


class FakeLister:
    """Liste pilotée par le test ; les fichiers sont lus depuis un dossier local."""

    def __init__(self, root: Path):
        self.root = root
        self.files: list[F] = []
        self.down = False
        self.refreshes = 0

    def put(self, name, data, size=None):
        path = self.root / FOLDER.lstrip("/") / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.files = [f for f in self.files if f.name != name]
        self.files.append(F(FOLDER, name, len(data) if size is None else size))

    def refresh(self):
        self.refreshes += 1

    def list_files(self):
        if self.down:
            raise SourceDisconnected("débranché")
        return list(self.files)


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "cadt.db")
    lister = FakeLister(tmp_path / "card")
    d = Downloader(db, DirectorySource(tmp_path / "card"), tmp_path / "sd")
    yield db, lister, Watcher(db, lister, d), tmp_path
    db.close()


# ------------------------------------------------------------------ stabilité

def test_file_needs_two_identical_sightings(env):
    db, lister, w, _ = env
    lister.put("DSC_0001.NEF", b"x" * 1000)

    r1 = w.poll_once()
    assert r1.new == 0 and r1.pending_stability == 1
    r2 = w.poll_once()
    assert r2.new == 1 and r2.saved == 1
    r3 = w.poll_once()
    assert r3.new == 0 and r3.outcomes == []
    assert lister.refreshes == 3


def test_file_seen_while_being_written_is_not_saved_truncated(env):
    """Le fichier grossit entre deux passages : seule la version finale est enregistrée."""
    db, lister, w, tmp = env
    full = bytes(range(256)) * 40
    lister.put("DSC_0001.NEF", full[:3000])
    w.poll_once()
    lister.put("DSC_0001.NEF", full[:7000])
    w.poll_once()
    lister.put("DSC_0001.NEF", full)
    w.poll_once()
    r = w.poll_once()

    assert r.saved == 1
    rows = db.conn.execute("SELECT name, size, state FROM photos").fetchall()
    assert [tuple(x) for x in rows] == [("DSC_0001.NEF", len(full), "verified")]
    saved = Path(db.conn.execute("SELECT local_path FROM photos").fetchone()[0])
    assert saved.read_bytes() == full


def test_vanished_candidate_is_forgotten(env):
    db, lister, w, _ = env
    lister.put("DSC_0001.NEF", b"x" * 10)
    w.poll_once()
    lister.files = []
    w.poll_once()
    lister.put("DSC_0001.NEF", b"x" * 10)
    assert w.poll_once().new == 0  # recompté depuis le début
    assert w.poll_once().new == 1


def test_stable_polls_one_registers_immediately(env):
    db, lister, _, tmp = env
    w = Watcher(db, lister, Downloader(db, DirectorySource(tmp / "card"), tmp / "sd"), stable_polls=1)
    lister.put("DSC_0001.NEF", b"x" * 10)
    assert w.poll_once().saved == 1


# ------------------------------------------------------------------ boucle

def test_run_survives_disconnection(env):
    db, lister, w, _ = env
    lister.put("DSC_0001.NEF", b"a" * 100)
    script = iter([False, True, True, False, False, False])  # état « débranché » à chaque passage
    polls = []

    def sleep(_):
        lister.down = next(script, False)

    def stop():
        polls.append(1)
        return len(polls) > 6

    w.run(interval=0, reconnect_delay=0, should_stop=stop, sleep=sleep)
    assert db.count_unsaved() == 0
    assert db.conn.execute("SELECT count(*) FROM photos").fetchone()[0] == 1


# ------------------------------------------------------------------ de bout en bout

def test_end_to_end_burst_with_gphoto2(tmp_path):
    """Le simulateur tire une rafale pendant que le watcher tourne, via gphoto2 / Directory Browse."""
    pytest.importorskip("gphoto2")
    from cadt.gphoto_source import Gphoto2Source

    card = tmp_path / "card"
    sim = SimulatedCamera(card, raw_size=200_000, jpeg_size=50_000)
    sim.shoot()  # la carte existe et contient déjà une photo

    source = Gphoto2Source.directory(str(card))
    with Database(tmp_path / "cadt.db") as db, source:
        w = Watcher(db, source, Downloader(db, source, tmp_path / "sd"))
        stop = threading.Event()

        def shoot():
            sim.burst(seconds=1.5, fps=7)
            stop.set()

        t = threading.Thread(target=shoot)
        t.start()
        while not stop.is_set():
            w.poll_once()
        t.join()
        for _ in range(3):  # quelques passages après la rafale, pour la stabilité
            w.poll_once()

        on_card = sorted(p for p in (card / "DCIM").rglob("DSC_*"))
        assert len(on_card) == 2 * 11  # 1 + 10 prises (1,5 s × 7 i/s ≈ 10), RAW + JPEG
        rows = db.conn.execute("SELECT name, sha256, state, local_path FROM photos").fetchall()
        assert len(rows) == len(on_card)  # aucun doublon
        by_name = {r["name"]: r for r in rows}
        for f in on_card:
            r = by_name[f.name]
            assert r["state"] == "verified"
            assert r["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
            assert Path(r["local_path"]).read_bytes() == f.read_bytes()
