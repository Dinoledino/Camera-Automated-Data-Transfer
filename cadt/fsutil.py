"""Outils de fichiers partagés : hash relu depuis le disque, fsync de dossier."""

from __future__ import annotations

import errno
import hashlib
import os
from pathlib import Path

CHUNK_SIZE = 1024 * 1024


def sha256_file(path: str | Path, drop_cache: bool = True) -> str:
    """Hash du fichier tel qu'il est sur le disque.

    Avec drop_cache, on demande au noyau d'oublier les pages en cache avant de lire,
    pour relire réellement la microSD et pas une copie en RAM. Ça marche pour les pages
    déjà écrites (d'où le fsync avant). Sur la microSD de l'A50 : À VÉRIFIER.
    """
    h = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY)
    try:
        if drop_cache and hasattr(os, "posix_fadvise"):
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        with os.fdopen(fd, "rb", closefd=False) as f:
            while chunk := f.read(CHUNK_SIZE):
                h.update(chunk)
    finally:
        os.close(fd)
    return h.hexdigest()


def fsync_dir(path: str | Path) -> None:
    """Rend durable la création ou le renommage d'une entrée du dossier.

    Certains systèmes de fichiers (FUSE, sdcardfs…) refusent le fsync d'un dossier :
    on l'accepte, faute de mieux. Comportement sur l'exFAT de l'A50 : À VÉRIFIER.
    """
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError as e:
        if e.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EBADF):
            raise
    finally:
        os.close(fd)


def remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
