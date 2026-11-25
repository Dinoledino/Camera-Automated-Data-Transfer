"""Requêtes de l'interface : galerie, compteurs, état de la copie, voisins en plein écran.

Lecture seule ; les modifications passent par Database (journalisées).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from ..db import Database, Photo

# Photos visibles dans la galerie : copiées et contrôlées, hors corbeille.
VISIBLE = "state IN ('verified', 'synced_pc') AND trashed_at IS NULL AND purged_at IS NULL"

FILTERS = {
    "toutes": ("Toutes", "1"),
    "nontriees": ("Non triées", "flag = 'none' AND favorite = 0"),
    "favoris": ("Favoris", "favorite = 1"),
    "gardees": ("Gardées", "flag = 'keep'"),
    "rejetees": ("Rejetées", "flag = 'reject'"),
}
ORDER = "capture_time DESC, id DESC"
WATCHER_STALE_SECONDS = 30


@dataclass(frozen=True)
class Scope:
    """Ce que la galerie montre : un filtre, et une session (None : toutes)."""
    filter: str = "toutes"
    session_id: int | None = None

    def where(self) -> tuple[str, list]:
        sql = f"{VISIBLE} AND ({FILTERS.get(self.filter, FILTERS['toutes'])[1]})"
        params: list = []
        if self.session_id is not None:
            sql += " AND session_id = ?"
            params.append(self.session_id)
        return sql, params


def photos(db: Database, scope: Scope, limit: int = 300, offset: int = 0) -> list[Photo]:
    where, params = scope.where()
    rows = db.conn.execute(f"SELECT * FROM photos WHERE {where} ORDER BY {ORDER} LIMIT ? OFFSET ?",
                           (*params, limit, offset)).fetchall()
    return [Photo(**dict(r)) for r in rows]


def filter_counts(db: Database, session_id: int | None) -> dict[str, int]:
    out = {}
    for key in FILTERS:
        where, params = Scope(key, session_id).where()
        out[key] = db.conn.execute(f"SELECT count(*) FROM photos WHERE {where}", params).fetchone()[0]
    return out


def neighbors(db: Database, scope: Scope, photo: Photo) -> tuple[int | None, int | None, int, int]:
    """(précédente, suivante, position 1-based, total) dans l'ordre de la galerie."""
    where, params = scope.where()
    ids = [r[0] for r in db.conn.execute(f"SELECT id FROM photos WHERE {where} ORDER BY {ORDER}", params)]
    if photo.id not in ids:
        return None, None, 0, len(ids)
    i = ids.index(photo.id)
    return (ids[i - 1] if i > 0 else None, ids[i + 1] if i + 1 < len(ids) else None, i + 1, len(ids))


@dataclass(frozen=True)
class CopyStatus:
    camera: str            # « branché », « non détecté », « inconnu »
    full: bool
    total: int             # photos vues sur l'appareil (hors abandonnées)
    saved: int
    copying: int           # en cours de copie
    waiting: int           # en attente
    failed: int            # abandonnées après plusieurs échecs
    deleted_on_camera: int
    only_on_phone: int     # copiées, effacées de l'appareil, pas encore sur le PC

    @property
    def unsaved(self) -> int:
        return self.copying + self.waiting + self.failed

    @property
    def percent(self) -> int:
        return round(100 * self.saved / self.total) if self.total else 100


def copy_status(db: Database, session_id: int | None = None,
                now: datetime | None = None) -> CopyStatus:
    cond, params = ("AND session_id = ?", [session_id]) if session_id is not None else ("", [])
    q = lambda where: db.conn.execute(f"SELECT count(*) FROM photos WHERE {where} {cond}", params).fetchone()[0]  # noqa: E731
    raw = db.get_setting("watcher_status")
    camera, full = "inconnu", False
    if raw:
        st = json.loads(raw)
        age = ((now or datetime.now(timezone.utc)) - datetime.fromisoformat(st["at"])).total_seconds()
        full = bool(st.get("full"))
        camera = "branché" if st.get("camera") == "connecté" and age <= WATCHER_STALE_SECONDS else "non détecté"
    saved = q("state IN ('verified', 'synced_pc')")
    copying = q("state IN ('downloading', 'downloaded')")
    waiting = q("state = 'detected' AND given_up_at IS NULL")
    failed = q("state = 'detected' AND given_up_at IS NOT NULL")
    return CopyStatus(
        camera=camera, full=full, total=saved + copying + waiting + failed, saved=saved,
        copying=copying, waiting=waiting, failed=failed,
        deleted_on_camera=q("delete_state = 'deleted_on_camera'"),
        only_on_phone=q("delete_state = 'deleted_on_camera' AND state = 'verified' AND purged_at IS NULL"),
    )


def rejected_count(db: Database) -> int:
    return db.conn.execute(f"SELECT count(*) FROM photos WHERE {VISIBLE} AND flag = 'reject'").fetchone()[0]


def media_row(db: Database, photo_id: int):
    return db.conn.execute("SELECT * FROM media WHERE photo_id = ?", (photo_id,)).fetchone()
