"""Superviseur : lance la sauvegarde, les aperçus et l'interface, et relance ce qui s'arrête.

    python -m cadt.supervise --db cadt.db --dest MÉMOIRE_INTERNE --dest MICROSD [--card SIMULATEUR]

Trois processus séparés, pour qu'un plantage de l'un n'arrête jamais les autres :
- « sauvegarde »  : cadt.watcher (copie, vérification, effacement de l'appareil, corbeille) ;
- « aperçus »     : cadt.indexer (miniatures et aperçus : traitement d'image en code compilé) ;
- « interface »   : cadt.web.

Pourquoi : avec Python 3.14.10 de Termux, un plantage intermittent du code compilé a été constaté
pendant le traitement d'image (ramasse-miettes en cause, 2026-11-25). La sauvegarde, elle, reprend
proprement après un arrêt brutal (testé avec kill -9) : il suffit de la relancer.

Chaque relance est notée dans le journal, en langage clair. Les relances sont espacées si un
processus plante en boucle (1 s, 2 s, 4 s… jusqu'à 60 s ; remis à 1 s après une minute sans plantage).
"""

from __future__ import annotations

import argparse
import logging
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .db import Database

log = logging.getLogger("cadt.supervise")

STABLE_AFTER = 60.0
MAX_BACKOFF = 60.0


@dataclass
class Child:
    name: str
    label: str                 # pour le journal : « La copie des photos »
    argv: list[str]
    proc: subprocess.Popen | None = None
    started: float = 0.0
    backoff: float = 1.0
    restart_at: float = 0.0
    restarts: int = 0
    history: list[int] = field(default_factory=list)  # codes de sortie


class Supervisor:
    def __init__(self, children: list[Child], db_path: Path | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.children = children
        self.db_path = db_path
        self.clock = clock
        self.stopping = False

    def _journal(self, kind: str, message: str, data: dict | None = None) -> None:
        if self.db_path is None:
            return
        try:
            with Database(self.db_path) as db:
                db.log_event(kind, message=message, data=data)
        except Exception:  # le journal ne doit jamais empêcher la relance
            log.exception("journal indisponible")

    def start(self, child: Child) -> None:
        child.proc = subprocess.Popen(child.argv)
        child.started = self.clock()
        log.info("%s : démarré (pid %d)", child.name, child.proc.pid)

    def step(self) -> None:
        """Un tour de surveillance : relance ce qui s'est arrêté (quand son délai est passé)."""
        now = self.clock()
        for c in self.children:
            if c.proc is None:
                if now >= c.restart_at:
                    self.start(c)
                continue
            code = c.proc.poll()
            if code is None:
                if now - c.started >= STABLE_AFTER:
                    c.backoff = 1.0
                continue
            c.history.append(code)
            c.proc = None
            if self.stopping:
                continue
            c.restarts += 1
            c.restart_at = now + c.backoff
            what = "s'est arrêtée" if code == 0 else "s'est arrêtée de façon inattendue"
            self._journal("process_restart",
                          f"{c.label} {what} : relancée automatiquement",
                          {"process": c.name, "exit_code": code, "delay_s": c.backoff})
            log.warning("%s : arrêté (code %s), relance dans %.0f s", c.name, code, c.backoff)
            c.backoff = min(c.backoff * 2, MAX_BACKOFF)

    def stop(self, timeout: float = 10.0) -> None:
        self.stopping = True
        for c in self.children:
            if c.proc is not None and c.proc.poll() is None:
                c.proc.terminate()
        deadline = time.monotonic() + timeout
        for c in self.children:
            if c.proc is not None:
                try:
                    c.proc.wait(max(deadline - time.monotonic(), 0.1))
                except subprocess.TimeoutExpired:
                    c.proc.kill()
                    c.proc.wait()

    def run(self, should_stop: Callable[[], bool] = lambda: False, tick: float = 0.5) -> None:
        try:
            while not should_stop():
                self.step()
                time.sleep(tick)
        finally:
            self.stop()


def build_children(args: argparse.Namespace) -> list[Child]:
    py = [sys.executable, "-m"]
    dest = [x for d in args.dest for x in ("--dest", str(d))]
    reserve = ["--reserve-gb", str(args.reserve_gb)]
    watcher = py + ["cadt.watcher", "--db", str(args.db), *dest, *reserve]
    if args.card:
        watcher += ["--card", str(args.card)]
    return [
        Child("sauvegarde", "La copie des photos", watcher),
        Child("aperçus", "La préparation des aperçus", py + ["cadt.indexer", "--db", str(args.db), *dest]),
        Child("interface", "L'interface", py + ["cadt.web", "--db", str(args.db), *dest, *reserve,
                                                 "--host", args.host, "--port", str(args.port)]),
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Lance et surveille la sauvegarde, les aperçus et l'interface.")
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--dest", type=Path, required=True, action="append",
                   help="emplacements de sauvegarde, dans l'ordre de remplissage")
    p.add_argument("--reserve-gb", type=float, default=2.0)
    p.add_argument("--card", type=Path, help="simulateur : ce dossier joue le rôle de l'appareil")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    with Database(args.db):
        pass  # crée ou met à jour la base avant que les trois processus ne s'y connectent
    sup = Supervisor(build_children(args), args.db)
    stop = False

    def on_signal(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    log.info("interface : http://%s:%d", args.host, args.port)
    sup.run(should_stop=lambda: stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
