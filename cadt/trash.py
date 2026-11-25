"""Corbeille du téléphone : rejeter une photo = vouloir la supprimer, en trois temps.

1. sélection : la photo est marquée « rejetée » (flag) ;
2. confirmation dans l'interface, puis `put()` : le fichier est déplacé (renommage atomique)
   dans `<emplacement>/.cadt/corbeille/`, sur le même emplacement de stockage ;
3. récupérable par `restore()` jusqu'à `expire()`, qui la supprime définitivement quand :
   - le délai est écoulé (réglage trash_delay_hours, 24 h par défaut), ou
   - le téléphone a redémarré depuis la mise à la corbeille (identifiant de démarrage du noyau
     différent : « jusqu'à l'extinction » ; un simple redémarrage de l'application ne compte pas).
   Téléphone plein : `free_space()` vide la corbeille en premier, des plus anciennes aux plus
   récentes (c'est la seule exception au délai).

Les photos rejetées ne sont plus sur l'appareil photo (effacé après vérification) : la
suppression définitive est irréversible. À l'inverse, une photo supprimée du téléphone n'est
jamais effacée de l'appareil ensuite (voir Database.camera_deletion_candidates).

Déplacements par le mécanisme pending_moves (reprise après coupure), puis `recover()` au
démarrage pour les étapes interrompues propres à la corbeille.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

from .db import Database, InvalidTransition, Photo, iso_utc
from .fsutil import fsync_dir, remove_quietly
from .sessions import dated_folder, free_path
from .storage import StoragePool

TRASH_DIR = Path(".cadt") / "corbeille"
BOOT_ID_FILE = Path("/proc/sys/kernel/random/boot_id")
DEFAULT_DELAY_HOURS = 24.0


def read_boot_id() -> str | None:
    """Identifiant du démarrage en cours (change à chaque démarrage du téléphone), ou None."""
    try:
        return BOOT_ID_FILE.read_text().strip() or None
    except OSError:
        return None


class Trash:
    def __init__(self, db: Database, storage: "str | Path | StoragePool",
                 boot_id: Callable[[], str | None] = read_boot_id,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.db = db
        self.pool = StoragePool.of(storage)
        self.boot_id = boot_id
        self.now = now

    # ---------------------------------------------------------- réglage

    @property
    def delay(self) -> timedelta:
        return timedelta(hours=float(self.db.get_setting("trash_delay_hours", str(DEFAULT_DELAY_HOURS))))

    def set_delay_hours(self, hours: float) -> None:
        self.db.set_setting("trash_delay_hours", f"{hours:g}")

    def trash_dir(self, local_path: str) -> Path:
        return self.pool.root_of(local_path) / TRASH_DIR

    # ---------------------------------------------------------- mettre à la corbeille

    def put(self, photo_ids: Iterable[int]) -> list[Photo]:
        """Étape 3 du rejet (après confirmation) : les photos passent dans la corbeille."""
        done = []
        for photo_id in photo_ids:
            photo = self.db.get(photo_id)
            if photo is None or not photo.is_saved or photo.trashed_at or photo.purged_at:
                continue
            src = Path(photo.local_path)
            dst = self.trash_dir(photo.local_path) / f"{photo.id}_{src.name}"
            self._move(photo, src, dst)
            done.append(self.db.mark_trashed(photo.id, str(src), self.boot_id(), iso_utc(self.now())))
        return done

    def restore(self, photo_ids: Iterable[int]) -> list[Photo]:
        """Remet les photos à leur place d'origine (ou à côté, si le nom a été repris entre-temps)."""
        done = []
        for photo_id in photo_ids:
            photo = self.db.get(photo_id)
            if photo is None or not photo.in_trash:
                continue
            origin = Path(photo.trash_origin or self._guess_origin(photo))
            dst = free_path(self.db, origin, photo.id)
            self._move(photo, Path(photo.local_path), dst)
            done.append(self.db.mark_restored(photo.id))
        return done

    def _move(self, photo: Photo, src: Path, dst: Path) -> None:
        self.db.begin_move(photo.id, str(dst), photo.session_id, photo.session_number)
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        fsync_dir(dst.parent)
        fsync_dir(src.parent)
        self.db.finish_move(photo.id)

    def _guess_origin(self, photo: Photo) -> str:
        name = Path(photo.local_path).name.split("_", 1)[-1]
        return str(self.pool.root_of(photo.local_path) / dated_folder(photo) / name)

    # ---------------------------------------------------------- suppression définitive

    def purge(self, photo_ids: Iterable[int], reason: str = "corbeille vidée") -> int:
        """Supprime définitivement du téléphone. Renvoie le nombre d'octets libérés."""
        freed = 0
        for photo_id in photo_ids:
            photo = self.db.get(photo_id)
            if photo is None or not photo.in_trash:
                continue
            path = Path(photo.local_path)
            try:
                freed += path.stat().st_size
            except OSError:
                pass
            remove_quietly(path)
            self._remove_media(photo.id)
            self.db.mark_purged(photo.id, reason)
        return freed

    def _remove_media(self, photo_id: int) -> None:
        row = self.db.conn.execute("SELECT thumb_path, preview_path FROM media WHERE photo_id = ?",
                                   (photo_id,)).fetchone()
        if row is not None:
            for p in row:
                if p:
                    remove_quietly(Path(p))

    def expired(self) -> list[Photo]:
        now, boot = self.now(), self.boot_id()
        limit = now - self.delay
        out = []
        for p in self.db.trashed_photos():
            rebooted = boot is not None and p.trash_boot is not None and p.trash_boot != boot
            if rebooted or datetime.fromisoformat(p.trashed_at) <= limit:
                out.append(p)
        return out

    def expire(self) -> int:
        """Supprime définitivement ce qui a dépassé le délai ou survécu à une extinction."""
        items = self.expired()
        if items:
            self.purge([p.id for p in items], "délai de la corbeille écoulé ou téléphone éteint")
        return len(items)

    def free_space(self, target_bytes: int = 512 * 1024 * 1024) -> int:
        """Téléphone plein : vide la corbeille, des plus anciennes aux plus récentes, jusqu'à
        avoir libéré target_bytes (ou plus rien à vider). Renvoie les octets libérés."""
        freed = 0
        for p in self.db.trashed_photos():
            if freed >= target_bytes:
                break
            freed += self.purge([p.id], "mémoire du téléphone pleine : corbeille vidée en premier")
        return freed

    def seconds_left(self, photo: Photo) -> float:
        """Temps restant avant la suppression définitive (hors extinction du téléphone)."""
        end = datetime.fromisoformat(photo.trashed_at) + self.delay
        return max((end - self.now()).total_seconds(), 0.0)

    # ---------------------------------------------------------- reprise

    def recover(self) -> None:
        """Au démarrage, après Downloader.recover() (qui termine les déplacements interrompus) :
        - fichier arrivé dans la corbeille mais pas encore noté → noté maintenant ;
        - photo en corbeille dont le fichier a disparu → notée supprimée."""
        rows = self.db.conn.execute(
            "SELECT * FROM photos WHERE purged_at IS NULL AND trashed_at IS NULL AND local_path IS NOT NULL"
        ).fetchall()
        for r in rows:
            photo = Photo(**dict(r))
            if f"/{TRASH_DIR.as_posix()}/" in Path(photo.local_path).as_posix():
                try:
                    self.db.mark_trashed(photo.id, self._guess_origin(photo), None, iso_utc(self.now()))
                except InvalidTransition:
                    pass
        for photo in self.db.trashed_photos():
            if not Path(photo.local_path).exists():
                self._remove_media(photo.id)
                self.db.mark_purged(photo.id, "fichier absent de la corbeille au démarrage")
