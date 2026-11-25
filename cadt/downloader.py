"""Téléchargement vérifié des photos, de l'appareil vers la microSD (phase 3, étape 2).

Pour une photo, dans cet ordre :
1. écrire le contenu dans un fichier `.part` caché, en calculant le SHA-256 au passage ;
2. vérifier la taille, puis faire un `fsync` du `.part` → état `downloaded` ;
3. vider le cache de pages du fichier, puis le relire depuis la microSD et recalculer le hash ;
4. si les deux hash sont identiques : renommer de façon atomique le `.part` en fichier final,
   faire un `fsync` du dossier → état `verified`.
   Sinon : supprimer le `.part`, et la photo repart dans la file.

Seul le renommage rend le fichier « réel ». Après une coupure à n'importe quelle étape,
`recover()` remet les photos interrompues dans la file et supprime les `.part` orphelins.
Si la coupure arrive entre le renommage et l'écriture en base, la photo est
retéléchargée et le fichier final, identique, est remplacé.
"""

from __future__ import annotations

import errno
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Protocol

from .db import Database, HashMismatch, Photo
from .fsutil import CHUNK_SIZE, remove_quietly, fsync_dir, sha256_file
from .sessions import download_path, recover_moves

PART_SUFFIX = ".part"


# ------------------------------------------------------------------ source

class SourceError(Exception):
    """Échec de lecture d'un fichier sur l'appareil ; les autres fichiers restent lisibles."""


class SourceDisconnected(SourceError):
    """L'appareil n'est plus joignable (débranché, éteint) : inutile de continuer la file."""


class Source(Protocol):
    """Ce que le downloader attend d'un appareil photo (gphoto2, faux Nikon, dossier local…)."""

    def iter_chunks(self, folder: str, name: str) -> Iterable[bytes]:
        """Renvoie le contenu du fichier, morceau par morceau.

        Lève SourceError (ou SourceDisconnected) en cas d'échec.
        """
        ...


class DirectorySource:
    """Source de test : les « dossiers de l'appareil » sont des dossiers d'un disque local."""

    def __init__(self, root: str | Path, chunk_size: int = CHUNK_SIZE):
        self.root = Path(root)
        self.chunk_size = chunk_size

    def iter_chunks(self, folder: str, name: str) -> Iterator[bytes]:
        path = self.root / folder.lstrip("/") / name
        try:
            with open(path, "rb") as f:
                while chunk := f.read(self.chunk_size):
                    yield chunk
        except FileNotFoundError as e:
            raise SourceError(f"{folder}/{name} introuvable") from e
        except OSError as e:
            raise SourceDisconnected(str(e)) from e


# ------------------------------------------------------------------ résultats

class DiskFull(Exception):
    """Plus de place sur la microSD : on arrête la file proprement."""


@dataclass(frozen=True)
class Outcome:
    photo: Photo
    ok: bool
    error: str | None = None


# ------------------------------------------------------------------ downloader

class Downloader:
    def __init__(self, db: Database, source: Source, dest_root: str | Path):
        self.db = db
        self.source = source
        self.dest_root = Path(dest_root)

    # ---------------------------------------------------------- chemins

    def part_path(self, photo: Photo) -> Path:
        """Fichier temporaire caché, propre à la photo (l'id évite toute collision)."""
        final = self.final_path(photo)
        return final.with_name(f".{final.name}.{photo.id}{PART_SUFFIX}")

    def final_path(self, photo: Photo) -> Path:
        """Dossier et nom selon la session de la photo (voir cadt.sessions).

        Le D850 recommence à DSC_0001 après 9999 : deux photos différentes peuvent avoir
        le même nom le même jour. On ne remplace jamais le fichier d'une autre photo.
        """
        return download_path(self.db, self.dest_root, photo)

    # ---------------------------------------------------------- reprise

    def recover(self) -> list[Photo]:
        """À lancer au démarrage, avant toute autre opération."""
        lost = recover_moves(self.db)
        if lost:
            self.db.log_event("recover_lost_files", data={"photo_ids": lost})
        interrupted = self.db.reset_interrupted()
        if self.dest_root.exists():
            for part in self.dest_root.rglob(f".*{PART_SUFFIX}"):
                remove_quietly(part)
                self.db.log_event("orphan_part_removed", message=str(part))
        return interrupted

    # ---------------------------------------------------------- une photo

    def download(self, photo: Photo) -> Outcome:
        """Télécharge et vérifie une photo déjà passée en `downloading` (voir claim_next).

        Lève SourceDisconnected ou DiskFull après avoir remis la photo dans la file :
        ce sont des erreurs qui empêchent de traiter les photos suivantes.
        """
        final = self.final_path(photo)
        part = self.part_path(photo)
        try:
            final.parent.mkdir(parents=True, exist_ok=True)
            fsync_dir(final.parent.parent)
            sha = self._write_part(photo, part)
            self.db.mark_downloaded(photo.id, sha)

            reread = sha256_file(part)
            if reread != sha:
                # mark_verified journalise l'écart, remet la photo dans la file et lève HashMismatch.
                remove_quietly(part)
                try:
                    self.db.mark_verified(photo.id, str(final), reread)
                except HashMismatch as e:
                    return Outcome(self.db.get(photo.id), False, str(e))
                raise AssertionError("mark_verified aurait dû lever HashMismatch")

            os.replace(part, final)
            fsync_dir(final.parent)
            return Outcome(self.db.mark_verified(photo.id, str(final), reread), True)

        except SourceDisconnected as e:
            self._fail(photo, part, f"appareil déconnecté : {e}")
            raise
        except OSError as e:
            if e.errno in (errno.ENOSPC, errno.EDQUOT):
                self._fail(photo, part, "microSD pleine")
                raise DiskFull(str(e)) from e
            self._fail(photo, part, f"erreur disque : {e}")
            raise
        except SourceError as e:
            failed = self._fail(photo, part, str(e))
            return Outcome(failed, False, str(e))

    def _write_part(self, photo: Photo, part: Path) -> str:
        h = hashlib.sha256()
        written = 0
        with open(part, "wb") as f:
            for chunk in self.source.iter_chunks(photo.camera_folder, photo.name):
                f.write(chunk)
                h.update(chunk)
                written += len(chunk)
            f.flush()
            os.fsync(f.fileno())
        if written != photo.size:
            raise SourceError(f"taille reçue {written} octets, attendue {photo.size}")
        return h.hexdigest()

    def _fail(self, photo: Photo, part: Path, error: str) -> Photo:
        remove_quietly(part)
        return self.db.mark_failed(photo.id, error)

    # ---------------------------------------------------------- la file

    def drain(self, max_attempts: int = 5) -> list[Outcome]:
        """Vide la file jusqu'à ce qu'elle soit vide.

        S'arrête sur SourceDisconnected ou DiskFull (propagées à l'appelant).
        Une photo qui a déjà échoué max_attempts fois est laissée de côté (journalisée),
        pour ne pas bloquer les suivantes : elle reste « detected » et non sauvegardée.
        """
        outcomes: list[Outcome] = []
        skipped: set[int] = set()
        while True:
            photo = self.db.claim_next(exclude=skipped)
            if photo is None:
                return outcomes
            if photo.attempts > max_attempts:
                self.db.mark_failed(photo.id, f"abandonnée après {max_attempts} tentatives")
                skipped.add(photo.id)
                continue
            outcomes.append(self.download(photo))
