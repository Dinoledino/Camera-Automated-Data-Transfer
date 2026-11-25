"""Watcher : repère les nouvelles photos sur l'appareil et fait tourner la file (phase 3, étape 4).

Mode « polling » : à chaque passage, on relit la liste des fichiers de l'appareil, on
enregistre les nouveaux, puis on vide la file de téléchargement. Le mode « events »
(événement PTP « nouvel objet ») viendra quand on pourra le tester sur un vrai appareil :
le pilote Directory Browse du simulateur n'émet aucun événement.

Règle de stabilité : un fichier n'est enregistré qu'après avoir été vu au moins
`stable_polls` fois de suite avec la même taille et la même date. Sans elle, un fichier
vu pendant son écriture serait enregistré avec une taille partielle, téléchargé tronqué…
et « vérifié », puisque le hash porte sur ce qui a été lu. Sur un vrai appareil PTP, un
fichier n'apparaît normalement qu'une fois écrit : la règle coûte alors un passage de délai.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

from .db import Database, utc_now
from .downloader import DiskFull, Downloader, Outcome, SourceDisconnected, SourceError

if TYPE_CHECKING:
    from .cleaner import CameraCleaner
    from .indexer import Indexer
    from .trash import Trash

log = logging.getLogger("cadt.watcher")


class Lister(Protocol):
    def list_files(self) -> list: ...          # objets avec folder, name, size, capture_time, storage_id, key
    def refresh(self) -> None: ...


@dataclass
class PollResult:
    seen: int = 0
    new: int = 0
    pending_stability: int = 0
    outcomes: list[Outcome] = field(default_factory=list)
    indexed: int = 0
    deleted_on_camera: int = 0

    @property
    def saved(self) -> int:
        return sum(o.ok for o in self.outcomes)


class Watcher:
    def __init__(self, db: Database, lister: Lister, downloader: Downloader,
                 stable_polls: int = 2, refresh_each_poll: bool = True,
                 indexer: "Indexer | None" = None, index_budget_seconds: float = 1.0,
                 drain_budget_seconds: float = 10.0, drain_budget_raw_only_seconds: float = 60.0,
                 recent_window: int = 20, cleaner: "CameraCleaner | None" = None,
                 trash: "Trash | None" = None):
        self.db = db
        self.lister = lister
        self.downloader = downloader
        self.stable_polls = max(stable_polls, 1)
        self.refresh_each_poll = refresh_each_poll
        self.indexer = indexer
        self.index_budget_seconds = index_budget_seconds
        # Une vidange rend la main au bout de ce temps : on relit l'appareil, et les JPEG des
        # prises suivantes passent devant les NEF qui attendent encore. En RAW seul, il n'y a
        # rien à faire passer devant (les NEF partent dans l'ordre chronologique) : des tranches
        # plus longues évitent de relister l'appareil pour rien (coût mesuré par la sonde).
        self.drain_budget_seconds = drain_budget_seconds
        self.drain_budget_raw_only_seconds = drain_budget_raw_only_seconds
        self.recent_window = recent_window
        self.cleaner = cleaner                # effacement de l'appareil après vérification
        self.trash = trash                    # corbeille : expiration à chaque passage
        self._last_listing: list = []
        self._known: set[tuple] = set()               # déjà enregistrés en base
        self._candidates: dict[tuple[str, str], tuple[tuple, int]] = {}  # (dossier, nom) → (clé, vu n fois)

    def ignore_existing(self) -> int:
        """Considère comme déjà traitées toutes les photos présentes maintenant sur l'appareil.

        Pour la sonde : on ne veut sauvegarder que les photos prises pendant le test, pas
        toute la carte. À ne pas utiliser en production (la carte entière doit être sauvegardée).
        """
        self.lister.refresh()
        files = self.lister.list_files()
        self._known.update(f.key for f in files)
        return len(files)

    def poll_once(self) -> PollResult:
        """Un passage : liste, enregistre les fichiers stables, vide la file.

        Propage SourceDisconnected et DiskFull à l'appelant.
        """
        if self.refresh_each_poll:
            self.lister.refresh()
        files = self.lister.list_files()
        self._last_listing = files
        result = PollResult(seen=len(files))
        present: set[tuple[str, str]] = set()
        for f in files:
            if f.key in self._known:
                continue
            where = (f.folder, f.name)
            present.add(where)
            previous = self._candidates.get(where)
            count = previous[1] + 1 if previous and previous[0] == f.key else 1
            if count < self.stable_polls:
                self._candidates[where] = (f.key, count)
                result.pending_stability += 1
                continue
            self._candidates.pop(where, None)
            _, created = self.db.add_detected(f.folder, f.name, f.size, f.capture_time, f.storage_id)
            self._known.add(f.key)
            result.new += created
        # Un fichier disparu entre deux passages n'est plus candidat.
        for where in list(self._candidates):
            if where not in present:
                del self._candidates[where]
        result.outcomes = self.downloader.drain(max_seconds=self.current_drain_budget())
        if self.cleaner is not None:
            result.deleted_on_camera = len(self.cleaner.run(self._last_listing).deleted)
        if self.trash is not None:
            self.trash.expire()
        # L'indexation ne passe que quand plus rien n'attend d'être sauvegardé (pas de fichier
        # en cours de stabilisation, file vide), et seulement le temps imparti : sur l'A50, un
        # NEF coûte ~1,5 s d'indexation, qu'on ne veut pas voler à une rafale en cours.
        if self.indexer is not None and not self._candidates and not self._queue_has_ready_work():
            result.indexed = self.indexer.run(max_seconds=self.index_budget_seconds)
        return result

    def _status(self, camera: str, full: bool = False) -> None:
        """État lu par l'interface (processus séparé) : appareil, mémoire pleine, dernier passage."""
        self.db.conn.execute(
            "INSERT INTO app_state (key, value) VALUES ('watcher_status', ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (json.dumps({"camera": camera, "full": full, "at": utc_now()}),))

    def shooting_jpeg(self) -> bool:
        """Les dernières photos détectées comptent-elles au moins un JPEG ?"""
        return self.db.conn.execute(
            "SELECT 1 FROM (SELECT kind FROM photos ORDER BY id DESC LIMIT ?) WHERE kind = 'jpeg' LIMIT 1",
            (self.recent_window,)).fetchone() is not None

    def current_drain_budget(self) -> float:
        return self.drain_budget_seconds if self.shooting_jpeg() else self.drain_budget_raw_only_seconds

    def _queue_has_ready_work(self) -> bool:
        return self.db.conn.execute(
            "SELECT 1 FROM photos WHERE state IN ('detected', 'downloading') AND given_up_at IS NULL "
            "AND next_attempt_at IS NULL LIMIT 1").fetchone() is not None

    def run(self, interval: float = 2.0, reconnect_delay: float = 5.0,
            should_stop: Callable[[], bool] = lambda: False,
            sleep: Callable[[float], None] = time.sleep,
            full_retry_delay: float = 60.0,
            on_disk_full: Callable[[], int] | None = None) -> None:
        """Boucle principale : tourne jusqu'à should_stop().

        Téléphone plein : la copie s'arrête mais la boucle continue (l'interface reste utilisable,
        et la copie reprend dès qu'on libère de la place). on_disk_full() peut libérer de la place
        (ex. vider la corbeille) et renvoie le nombre d'octets libérés.
        """
        self.downloader.recover()
        self._status("démarrage")
        if self.trash is not None:
            self.trash.recover()
            self.trash.expire()
            if on_disk_full is None:
                on_disk_full = self.trash.free_space
        full = False
        while not should_stop():
            started = time.monotonic()
            try:
                r = self.poll_once()
                self._status("connecté", full=False)
                if full and r.saved:
                    full = False
                    self.db.log_event("storage_ok", message="de la place s'est libérée : la copie reprend")
                if r.new or r.outcomes:
                    log.info("%d nouvelles, %d sauvegardées, %d échecs, %d non sauvegardées au total",
                             r.new, r.saved, len(r.outcomes) - r.saved, self.db.count_unsaved())
                # L'intervalle compte depuis le début du passage : après une longue vidange,
                # on relit tout de suite (la stabilité reste mesurée sur des passages espacés).
                wait = interval - (time.monotonic() - started)
                retry = self.downloader.seconds_until_next_retry()
                if retry is not None:
                    wait = min(wait, max(retry, 0.1))
                if wait > 0:
                    sleep(wait)
            except SourceDisconnected as e:
                self._status("absent", full=full)
                log.warning("appareil injoignable (%s) ; nouvel essai dans %.0f s", e, reconnect_delay)
                self._candidates.clear()
                sleep(reconnect_delay)
            except DiskFull:
                freed = on_disk_full() if on_disk_full is not None else 0
                if freed:
                    log.warning("mémoire pleine : %d octets libérés, la copie reprend", freed)
                    continue
                if not full:
                    log.error("mémoire du téléphone pleine : la copie s'arrête, %d photos restent sur "
                              "l'appareil photo", self.db.count_unsaved())
                    self.db.log_event("storage_full", message="mémoire du téléphone pleine : copie arrêtée")
                full = True
                self._status("connecté", full=True)
                sleep(full_retry_delay)
                continue
            except SourceError as e:
                log.warning("erreur passagère pendant la liste : %s", e)
                sleep(interval)


# ------------------------------------------------------------------ ligne de commande

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sauvegarde au fil de l'eau des photos de l'appareil.")
    p.add_argument("--db", type=Path, required=True, help="base SQLite")
    p.add_argument("--dest", type=Path, required=True, action="append",
                   help="emplacement de sauvegarde ; à répéter, dans l'ordre de remplissage "
                        "(ex. --dest mémoire-interne --dest microSD)")
    p.add_argument("--reserve-gb", type=float, default=2.0,
                   help="marge gardée libre sur chaque emplacement (Go)")
    p.add_argument("--card", type=Path, help="lire ce dossier comme un appareil (simulateur)")
    p.add_argument("--interval", type=float, default=2.0, help="secondes entre deux passages")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    from .gphoto_source import Gphoto2Source  # importé ici : gphoto2 n'est requis qu'à l'exécution

    source = Gphoto2Source.directory(str(args.card)) if args.card else Gphoto2Source()
    stop = False

    def on_signal(*_: object) -> None:
        nonlocal stop
        stop = True
        log.info("arrêt demandé, fin du passage en cours…")

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    from . import exif
    exiftool = exif.ExifTool() if exif.available() else None
    if exiftool is None:
        log.warning("exiftool introuvable : pas d'indexation (miniatures, EXIF)")
    from .storage import GB, StoragePool
    pool = StoragePool(args.dest, reserve_bytes=int(args.reserve_gb * GB))
    with Database(args.db) as db:
        indexer = None
        if exiftool is not None:
            from .indexer import Indexer
            indexer = Indexer(db, pool.primary, exiftool)
        from .cleaner import CameraCleaner
        from .trash import Trash
        watcher = Watcher(db, source, Downloader(db, source, pool), indexer=indexer,
                          cleaner=CameraCleaner(db, source), trash=Trash(db, pool))
        try:
            watcher.run(interval=args.interval, should_stop=lambda: stop)
        finally:
            source.close()
            if exiftool is not None:
                exiftool.close()
        log.info("arrêté : %d photos non sauvegardées.", db.count_unsaved())
    return 0


if __name__ == "__main__":
    sys.exit(main())
