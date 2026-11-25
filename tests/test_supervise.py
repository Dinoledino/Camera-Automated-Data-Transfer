"""Superviseur : relance des processus arrêtés, journal, arrêt propre."""
import sys
import time

from cadt.db import Database
from cadt.supervise import Child, Supervisor


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def wait_exit(child, timeout=5):
    end = time.time() + timeout
    while child.proc.poll() is None and time.time() < end:
        time.sleep(0.02)


def test_crashing_child_is_restarted_with_backoff_and_logged(tmp_path):
    db = tmp_path / "c.db"
    Database(db).close()
    crash = Child("aperçus", "La préparation des aperçus",
                  [sys.executable, "-c", "import os; os.abort()"])
    clock = Clock()
    sup = Supervisor([crash], db, clock=clock)

    sup.step()                       # démarrage
    wait_exit(crash)
    sup.step()                       # constat du plantage → relance prévue dans 1 s
    assert crash.proc is None and crash.restarts == 1 and crash.history[-1] != 0
    sup.step()
    assert crash.proc is None        # pas encore
    clock.t = 1.0
    sup.step()                       # relancé
    assert crash.proc is not None
    wait_exit(crash)
    sup.step()
    assert crash.restart_at == 1.0 + 2.0  # délai doublé

    with Database(db) as d:
        ev = [e for e in d.events() if e["kind"] == "process_restart"]
    assert len(ev) == 2
    assert ev[0]["message"] == "La préparation des aperçus s'est arrêtée de façon inattendue : relancée automatiquement"
    sup.stop()


def test_stable_child_resets_backoff_and_stop_terminates(tmp_path):
    sleeper = Child("interface", "L'interface", [sys.executable, "-c", "import time; time.sleep(60)"])
    clock = Clock()
    sup = Supervisor([sleeper], clock=clock)
    sleeper.backoff = 16.0
    sup.step()
    clock.t = 61.0
    sup.step()
    assert sleeper.backoff == 1.0
    sup.stop(timeout=5)
    assert sleeper.proc.poll() is not None
    sup.step()                       # pendant l'arrêt : pas de relance
    assert sleeper.restarts == 0


def test_supervised_backup_survives_indexer_crash(tmp_path):
    """Bout en bout : la sauvegarde continue pendant que les aperçus plantent en boucle."""
    import pytest
    pytest.importorskip("gphoto2")  # le watcher lit la carte simulée par gphoto2 (Directory Browse)
    from cadt.simulator import SimulatedCamera
    card, sd, db = tmp_path / "card", tmp_path / "sd", tmp_path / "c.db"
    Database(db).close()
    sim = SimulatedCamera(card, raw_size=2000, slots=2)
    watcher = Child("sauvegarde", "La copie des photos",
                    [sys.executable, "-m", "cadt.watcher", "--db", str(db), "--dest", str(sd),
                     "--reserve-gb", "0", "--card", str(card), "--interval", "0.2"])
    broken = Child("aperçus", "La préparation des aperçus", [sys.executable, "-c", "import os; os.abort()"])
    sup = Supervisor([watcher, broken], db)
    try:
        sup.step()
        for _ in range(3):
            sim.shoot()
        deadline = time.time() + 30
        while time.time() < deadline:
            sup.step()
            with Database(db) as d:
                if d.conn.execute("SELECT count(*) FROM photos WHERE state = 'verified'").fetchone()[0] == 3:
                    break
            time.sleep(0.2)
        with Database(db) as d:
            assert d.conn.execute("SELECT count(*) FROM photos WHERE state = 'verified'").fetchone()[0] == 3
        assert broken.restarts >= 1 and watcher.restarts == 0
    finally:
        sup.stop()
