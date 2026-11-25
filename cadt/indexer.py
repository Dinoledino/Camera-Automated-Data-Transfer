"""Indexation des photos sauvegardées : EXIF, miniature et aperçu (phase 4).

Pour chaque photo vérifiée pas encore indexée :
- métadonnées lues par exiftool (date, objectif, focale, ouverture, vitesse, ISO…) ;
- miniature (~400 px) pour la galerie et aperçu (~2048 px) pour le plein écran,
  avec la rotation EXIF appliquée (photos en portrait) :
  * NEF : à partir des JPEG intégrés (PreviewImage 640 px, JpgFromRaw pleine taille) ;
  * JPEG : à partir du fichier lui-même.
- Décodage JPEG en mode « draft » (réduction pendant le décodage) : rapide, même pour
  un JpgFromRaw de 45 Mpx sur le téléphone.

Les images générées vont dans <racine>/.cadt/{thumbs,previews}/<id>.jpg. Elles sont
recalculables et ne remplacent jamais la photo. L'indexation ne passe qu'après les
téléchargements (voir Watcher) : elle ne retarde pas la sauvegarde.
"""

from __future__ import annotations

import io
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image

from .db import Database, Photo, utc_now
from .exif import ExifTool, ExifToolError, Shooting, parse, read_embedded

CACHE_DIR = ".cadt"
THUMB_SIZE = 400
PREVIEW_SIZE = 2048
JPEG_QUALITY = 85

# Valeur EXIF Orientation → transformation pour remettre l'image à l'endroit.
_ORIENT = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}


@dataclass(frozen=True)
class Media(Shooting):
    photo_id: int = 0
    thumb_path: str | None = None
    preview_path: str | None = None
    indexed_at: str = ""
    error: str | None = None


def resized(jpeg: bytes, size: int, orientation: int | None) -> Image.Image:
    im = Image.open(io.BytesIO(jpeg))
    im.draft("RGB", (size, size))  # le décodeur JPEG réduit directement (1/2, 1/4, 1/8)
    im = im.convert("RGB")
    im.thumbnail((size, size), Image.Resampling.LANCZOS)
    if orientation in _ORIENT:
        im = im.transpose(_ORIENT[orientation])
    return im


def save_atomic(im: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    im.save(tmp, "JPEG", quality=JPEG_QUALITY, optimize=True)
    os.replace(tmp, path)


class Indexer:
    def __init__(self, db: Database, dest_root: str | Path, exiftool: ExifTool,
                 thumb_size: int = THUMB_SIZE, preview_size: int = PREVIEW_SIZE):
        self.db = db
        self.dest_root = Path(dest_root)
        self.exiftool = exiftool
        self.thumb_size = thumb_size
        self.preview_size = preview_size

    def thumb_path(self, photo_id: int) -> Path:
        return self.dest_root / CACHE_DIR / "thumbs" / f"{photo_id}.jpg"

    def preview_path(self, photo_id: int) -> Path:
        return self.dest_root / CACHE_DIR / "previews" / f"{photo_id}.jpg"

    # ---------------------------------------------------------- file d'attente

    def pending(self, limit: int = 100) -> list[Photo]:
        """Photos sauvegardées sans index, les plus anciennes d'abord (JPEG avant RAW)."""
        rows = self.db.conn.execute(
            """SELECT p.* FROM photos p LEFT JOIN media m ON m.photo_id = p.id
               WHERE p.state IN ('verified', 'synced_pc') AND m.photo_id IS NULL
               ORDER BY CASE p.kind WHEN 'jpeg' THEN 0 WHEN 'raw' THEN 1 ELSE 2 END,
                        p.capture_time, p.id
               LIMIT ?""", (limit,)).fetchall()
        return [Photo(**dict(r)) for r in rows]

    def run(self, max_seconds: float | None = None, max_photos: int | None = None) -> int:
        """Indexe ce qui attend, dans la limite donnée. Renvoie le nombre de photos traitées."""
        start, done = time.monotonic(), 0
        while True:
            batch = self.pending(limit=20)
            if not batch:
                return done
            for photo in batch:
                if max_photos is not None and done >= max_photos:
                    return done
                if max_seconds is not None and time.monotonic() - start >= max_seconds:
                    return done
                self.index(photo)
                done += 1

    def reindex(self, photo_ids: list[int] | None = None) -> None:
        """Oublie l'index (toutes les photos si None, sinon celles-ci) : run() les refera."""
        with self.db.transaction() as c:
            if photo_ids is None:
                c.execute("DELETE FROM media")
            else:
                c.execute(f"DELETE FROM media WHERE photo_id IN ({','.join('?' * len(photo_ids))})",
                          photo_ids)

    # ---------------------------------------------------------- une photo

    def index(self, photo: Photo) -> Media:
        error = None
        shooting = parse({})
        thumb = preview = None
        try:
            meta = self.exiftool.read(photo.local_path)
            shooting = parse(meta)
            thumb, preview = self._images(photo, meta, shooting.orientation)
        except (ExifToolError, OSError, Image.DecompressionBombError, ValueError) as e:
            error = f"{type(e).__name__}: {e}"
        media = Media(**asdict(shooting), photo_id=photo.id,
                      thumb_path=str(thumb) if thumb else None,
                      preview_path=str(preview) if preview else None,
                      indexed_at=utc_now(), error=error)
        self._store(media)
        return media

    def _images(self, photo: Photo, meta: dict, orientation: int | None) -> tuple[Path | None, Path | None]:
        if photo.kind == "raw":
            small = read_embedded(photo.local_path, meta, "PreviewImage")
            large = read_embedded(photo.local_path, meta, "JpgFromRaw")
        elif photo.kind == "jpeg":
            small = large = Path(photo.local_path).read_bytes()
        else:
            return None, None  # vidéos et autres : métadonnées seulement
        large = large or small
        small = small or large
        if large is None:
            raise ValueError("aucun aperçu JPEG intégré dans le fichier")
        thumb, preview = self.thumb_path(photo.id), self.preview_path(photo.id)
        save_atomic(resized(small, self.thumb_size, orientation), thumb)
        save_atomic(resized(large, self.preview_size, orientation), preview)
        return thumb, preview

    def _store(self, m: Media) -> None:
        cols = [k for k in asdict(m)]
        with self.db.transaction() as c:
            c.execute(
                f"INSERT INTO media ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
                f"ON CONFLICT (photo_id) DO UPDATE SET "
                + ", ".join(f"{k} = excluded.{k}" for k in cols if k != "photo_id"),
                [getattr(m, k) for k in cols],
            )
            self.db._log(c, "indexed" if m.error is None else "index_error", m.photo_id, m.error)

    def get(self, photo_id: int) -> Media | None:
        row = self.db.conn.execute("SELECT * FROM media WHERE photo_id = ?", (photo_id,)).fetchone()
        return Media(**dict(row)) if row else None
