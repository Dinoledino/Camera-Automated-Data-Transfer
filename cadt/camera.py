"""Ce que le watcher sait d'un fichier de l'appareil, et une source « dossier » sans gphoto2."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class CameraFile:
    folder: str
    name: str
    size: int
    capture_time: str        # heure locale, ISO 8601 sans fuseau
    storage_id: str | None   # ex. store_00010001 (premier niveau de l'arborescence PTP)

    @property
    def key(self) -> tuple[str, int, str]:
        """Identité d'une photo, comme dans la base : (nom, taille, date)."""
        return (self.name, self.size, self.capture_time)


def storage_of(folder: str) -> str | None:
    first = folder.strip("/").split("/", 1)[0]
    return first if first.startswith("store_") else None


class DirectoryLister:
    """Liste un dossier comme le ferait gphoto2 avec « Directory Browse », sans gphoto2.

    Pour le banc de simulation et les tests (y compris sur le téléphone). Comme un appareil,
    les dossiers cachés (ex. .sim-tmp du simulateur) sont ignorés.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def refresh(self) -> None:
        pass

    def list_files(self) -> list[CameraFile]:
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            rel = "/" + os.path.relpath(dirpath, self.root).replace(os.sep, "/")
            folder = "/" if rel == "/." else rel
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                st = os.stat(os.path.join(dirpath, name))
                out.append(CameraFile(folder, name, st.st_size,
                                      datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                                      storage_of(folder)))
        return out
