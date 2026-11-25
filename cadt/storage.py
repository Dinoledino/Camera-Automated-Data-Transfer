"""Emplacements de stockage du téléphone, remplis l'un après l'autre (décision du 2026-11-25).

Typiquement : la mémoire interne, puis la microSD. Une photo va dans le premier emplacement qui
a assez de place en gardant une marge (`reserve_bytes`) pour la base, le journal et les aperçus.
Quand aucun n'a de place, la copie s'arrête : les photos restent sur l'appareil photo, qui n'est
effacé qu'après vérification.

Une photo ne change jamais d'emplacement quand on la range dans une autre session : un
déplacement reste un renommage atomique sur le même système de fichiers.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

GB = 1024 ** 3
DEFAULT_RESERVE = 2 * GB


@dataclass(frozen=True)
class Usage:
    root: Path
    total: int
    used: int
    free: int

    @property
    def percent_used(self) -> float:
        return 100.0 * self.used / self.total if self.total else 0.0


def _disk_usage(path: Path) -> tuple[int, int, int]:
    """(total, utilisé, libre) du système de fichiers qui contient `path` (même s'il n'existe pas encore)."""
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    u = shutil.disk_usage(p)
    return u.total, u.used, u.free


class StoragePool:
    def __init__(self, roots: Sequence[str | Path], reserve_bytes: int = DEFAULT_RESERVE,
                 usage_fn: Callable[[Path], tuple[int, int, int]] = _disk_usage):
        if not roots:
            raise ValueError("au moins un emplacement de stockage est nécessaire")
        self.roots = [Path(r) for r in roots]
        self.reserve_bytes = reserve_bytes
        self.usage_fn = usage_fn  # remplaçable dans les tests

    @classmethod
    def of(cls, dest: "str | Path | StoragePool") -> "StoragePool":
        """Accepte un pool, ou un dossier unique (sans marge : compatibilité, tests)."""
        return dest if isinstance(dest, StoragePool) else cls([dest], reserve_bytes=0)

    @property
    def primary(self) -> Path:
        """Le premier emplacement : il accueille aussi la base, le journal et les aperçus."""
        return self.roots[0]

    def usage(self) -> list[Usage]:
        out = []
        for r in self.roots:
            total, used, free = self.usage_fn(r)
            out.append(Usage(r, total, used, free))
        return out

    def choose(self, size: int) -> Path | None:
        """Premier emplacement où `size` octets tiennent en gardant la marge ; None si aucun."""
        for r in self.roots:
            _, _, free = self.usage_fn(r)
            if free - self.reserve_bytes >= size:
                return r
        return None

    def free_for_photos(self) -> int:
        """Place utilisable pour des photos, tous emplacements confondus (marges déduites)."""
        return sum(max(u.free - self.reserve_bytes, 0) for u in self.usage())

    def root_of(self, path: str | Path) -> Path:
        """L'emplacement qui contient ce fichier (le premier par défaut)."""
        p = Path(path)
        best = None
        for r in self.roots:
            if p == r or r in p.parents:
                if best is None or len(r.parts) > len(best.parts):
                    best = r
        return best or self.primary
