"""Couche d'accès SQLite : état des photos, file de téléchargement, journal d'événements.

Principes :
- mode WAL et synchronous=FULL : une transaction validée survit à une coupure de courant ;
- transactions courtes et explicites (BEGIN IMMEDIATE) ;
- chaque transition d'état est un UPDATE conditionnel (« WHERE state = <attendu> ») :
  une transition invalide lève InvalidTransition au lieu de corrompre l'état ;
- les règles critiques sont aussi garanties par des triggers SQL (voir schema.sql).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1
_SCHEMA_FILE = Path(__file__).with_name("schema.sql")

RAW_EXTENSIONS = {".nef", ".nrw"}
JPEG_EXTENSIONS = {".jpg", ".jpeg"}

# États où le fichier est sauvegardé et vérifié sur la microSD.
SAVED_STATES = ("verified", "synced_pc")


class InvalidTransition(Exception):
    """La photo n'est pas dans l'état attendu pour cette opération."""


class HashMismatch(Exception):
    """Le SHA-256 relu ne correspond pas à celui calculé au téléchargement."""


@dataclass(frozen=True)
class Photo:
    id: int
    camera_folder: str
    name: str
    size: int
    capture_time: str
    storage_id: str | None
    kind: str
    group_key: str
    state: str
    sha256: str | None
    local_path: str | None
    attempts: int
    last_error: str | None
    rating: int
    flag: str
    delete_state: str
    detected_at: str
    verified_at: str | None
    synced_at: str | None
    updated_at: str

    @property
    def is_saved(self) -> bool:
        return self.state in SAVED_STATES


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def classify_kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in RAW_EXTENSIONS:
        return "raw"
    if ext in JPEG_EXTENSIONS:
        return "jpeg"
    return "other"


def make_group_key(name: str, capture_time: str) -> str:
    """DSC_0001.NEF et DSC_0001.JPG pris au même instant partagent la même clé."""
    return f"{Path(name).stem.upper()}@{capture_time}"


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        # isolation_level=None : pas de transaction implicite, on les gère nous-mêmes.
        self.conn = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        if sqlite3.sqlite_version_info < (3, 35, 0):
            raise RuntimeError(f"SQLite >= 3.35 requis (RETURNING), trouvé {sqlite3.sqlite_version}")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = FULL")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ outils

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"Base en version {version}, code en version {SCHEMA_VERSION}")
        if version == 0:
            with self.transaction() as c:
                for statement in _split_sql(_SCHEMA_FILE.read_text(encoding="utf-8")):
                    c.execute(statement)
                c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _log(self, c: sqlite3.Connection, kind: str, photo_id: int | None = None,
             message: str | None = None, data: dict[str, Any] | None = None) -> None:
        c.execute(
            "INSERT INTO events (ts, photo_id, kind, message, data) VALUES (?, ?, ?, ?, ?)",
            (utc_now(), photo_id, kind, message, json.dumps(data) if data is not None else None),
        )

    def log_event(self, kind: str, photo_id: int | None = None,
                  message: str | None = None, data: dict[str, Any] | None = None) -> None:
        with self.transaction() as c:
            self._log(c, kind, photo_id, message, data)

    def _transition(self, c: sqlite3.Connection, photo_id: int, expected: tuple[str, ...],
                    sql_set: str, params: tuple[Any, ...]) -> Photo:
        placeholders = ",".join("?" * len(expected))
        row = c.execute(
            f"UPDATE photos SET {sql_set}, updated_at = ? "
            f"WHERE id = ? AND state IN ({placeholders}) RETURNING *",
            (*params, utc_now(), photo_id, *expected),
        ).fetchone()
        if row is None:
            current = c.execute("SELECT state FROM photos WHERE id = ?", (photo_id,)).fetchone()
            got = current["state"] if current else "introuvable"
            raise InvalidTransition(f"photo {photo_id} : état {got}, attendu {expected}")
        return Photo(**dict(row))

    # ------------------------------------------------------------- lecture

    def get(self, photo_id: int) -> Photo | None:
        row = self.conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        return Photo(**dict(row)) if row else None

    def find(self, name: str, size: int, capture_time: str) -> Photo | None:
        row = self.conn.execute(
            "SELECT * FROM photos WHERE name = ? AND size = ? AND capture_time = ?",
            (name, size, capture_time),
        ).fetchone()
        return Photo(**dict(row)) if row else None

    def count_unsaved(self) -> int:
        """Pour le compteur de l'interface : « N photos pas encore sauvegardées »."""
        return self.conn.execute(
            "SELECT count(*) FROM photos WHERE state NOT IN ('verified', 'synced_pc')"
        ).fetchone()[0]

    def events(self, photo_id: int | None = None) -> list[sqlite3.Row]:
        if photo_id is None:
            return self.conn.execute("SELECT * FROM events ORDER BY id").fetchall()
        return self.conn.execute(
            "SELECT * FROM events WHERE photo_id = ? ORDER BY id", (photo_id,)
        ).fetchall()

    # ------------------------------------------------------------- détection

    def add_detected(self, camera_folder: str, name: str, size: int, capture_time: str,
                     storage_id: str | None = None) -> tuple[Photo, bool]:
        """Enregistre une photo vue sur l'appareil. Idempotent : renvoie (photo, créée ?)."""
        now = utc_now()
        with self.transaction() as c:
            row = c.execute(
                """INSERT INTO photos (camera_folder, name, size, capture_time, storage_id,
                                       kind, group_key, detected_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (name, size, capture_time) DO NOTHING
                   RETURNING *""",
                (camera_folder, name, size, capture_time, storage_id,
                 classify_kind(name), make_group_key(name, capture_time), now, now),
            ).fetchone()
            if row is not None:
                photo = Photo(**dict(row))
                self._log(c, "detected", photo.id, f"{camera_folder}/{name}")
                return photo, True
            row = c.execute(
                "SELECT * FROM photos WHERE name = ? AND size = ? AND capture_time = ?",
                (name, size, capture_time),
            ).fetchone()
            return Photo(**dict(row)), False

    # ------------------------------------------------------------- file

    def claim_next(self) -> Photo | None:
        """Prend atomiquement la prochaine photo à télécharger : JPEG > RAW > autres, puis chronologique."""
        with self.transaction() as c:
            row = c.execute(
                """UPDATE photos
                   SET state = 'downloading', attempts = attempts + 1, updated_at = ?
                   WHERE id = (
                       SELECT id FROM photos
                       WHERE state = 'detected'
                       ORDER BY CASE kind WHEN 'jpeg' THEN 0 WHEN 'raw' THEN 1 ELSE 2 END,
                                capture_time, id
                       LIMIT 1)
                   RETURNING *""",
                (utc_now(),),
            ).fetchone()
            if row is None:
                return None
            photo = Photo(**dict(row))
            self._log(c, "download_started", photo.id, data={"attempt": photo.attempts})
            return photo

    def mark_downloaded(self, photo_id: int, sha256: str) -> Photo:
        """Le .part est écrit et fsync ; sha256 = hash calculé pendant le téléchargement."""
        sha256 = sha256.lower()
        with self.transaction() as c:
            photo = self._transition(c, photo_id, ("downloading",),
                                     "state = 'downloaded', sha256 = ?, last_error = NULL", (sha256,))
            self._log(c, "downloaded", photo_id, data={"sha256": sha256})
            return photo

    def mark_verified(self, photo_id: int, local_path: str, reread_sha256: str) -> Photo:
        """À appeler APRÈS relecture depuis la microSD et renommage du .part.

        Si le hash relu diffère, la photo repart en « detected » et HashMismatch est levée.
        """
        reread_sha256 = reread_sha256.lower()
        with self.transaction() as c:
            current = c.execute("SELECT state, sha256 FROM photos WHERE id = ?", (photo_id,)).fetchone()
            if current is None or current["state"] != "downloaded":
                got = current["state"] if current else "introuvable"
                raise InvalidTransition(f"photo {photo_id} : état {got}, attendu ('downloaded',)")
            if current["sha256"] != reread_sha256:
                self._transition(c, photo_id, ("downloaded",),
                                 "state = 'detected', sha256 = NULL, last_error = ?",
                                 ("hash mismatch on reread",))
                self._log(c, "hash_mismatch", photo_id,
                          data={"expected": current["sha256"], "reread": reread_sha256})
                mismatch = HashMismatch(f"photo {photo_id} : {current['sha256']} != {reread_sha256}")
            else:
                photo = self._transition(c, photo_id, ("downloaded",),
                                         "state = 'verified', local_path = ?, verified_at = ?",
                                         (local_path, utc_now()))
                self._log(c, "verified", photo_id, local_path)
                return photo
        raise mismatch

    def mark_failed(self, photo_id: int, error: str) -> Photo:
        """Échec pendant le téléchargement : la photo retourne dans la file."""
        with self.transaction() as c:
            photo = self._transition(c, photo_id, ("downloading", "downloaded"),
                                     "state = 'detected', sha256 = NULL, last_error = ?", (error,))
            self._log(c, "error", photo_id, error)
            return photo

    def mark_synced(self, photo_id: int, pc_sha256: str) -> Photo:
        """Le PC a recalculé le hash : il doit être identique à celui de la sauvegarde."""
        pc_sha256 = pc_sha256.lower()
        with self.transaction() as c:
            current = self.get(photo_id)
            if current is not None and current.state == "verified" and current.sha256 != pc_sha256:
                # On journalise puis on lève APRÈS le COMMIT, sinon l'événement serait annulé.
                self._log(c, "sync_hash_mismatch", photo_id,
                          data={"expected": current.sha256, "pc": pc_sha256})
                mismatch = HashMismatch(f"photo {photo_id} : copie PC différente")
            else:
                photo = self._transition(c, photo_id, ("verified",),
                                         "state = 'synced_pc', synced_at = ?", (utc_now(),))
                self._log(c, "synced_pc", photo_id)
                return photo
        raise mismatch

    def reset_interrupted(self) -> list[Photo]:
        """Au démarrage : les téléchargements interrompus repartent dans la file.

        Renvoie les photos concernées ; l'appelant supprime leurs .part orphelins.
        """
        with self.transaction() as c:
            rows = c.execute(
                """UPDATE photos
                   SET state = 'detected', sha256 = NULL, last_error = 'interrupted', updated_at = ?
                   WHERE state IN ('downloading', 'downloaded')
                   RETURNING *""",
                (utc_now(),),
            ).fetchall()
            photos = [Photo(**dict(r)) for r in rows]
            for p in photos:
                self._log(c, "reset_interrupted", p.id)
            return photos

    # ------------------------------------------------------------- tri

    def set_rating(self, photo_id: int, rating: int) -> None:
        with self.transaction() as c:
            c.execute("UPDATE photos SET rating = ?, updated_at = ? WHERE id = ?",
                      (rating, utc_now(), photo_id))
            self._log(c, "rating", photo_id, data={"rating": rating})

    def set_flag(self, photo_id: int, flag: str) -> None:
        with self.transaction() as c:
            c.execute("UPDATE photos SET flag = ?, updated_at = ? WHERE id = ?",
                      (flag, utc_now(), photo_id))
            self._log(c, "flag", photo_id, data={"flag": flag})

    # ------------------------------------------------------------- suppression

    def request_delete(self, photo_id: int) -> None:
        """Étape 1 (marquer). Refusé si la photo n'est pas vérifiée (aussi garanti par trigger)."""
        with self.transaction() as c:
            current = self.get(photo_id)
            if current is None:
                raise InvalidTransition(f"photo {photo_id} introuvable")
            if current.delete_state != "none":
                return  # déjà demandée ou déjà supprimée : idempotent
            if not current.is_saved:
                raise InvalidTransition(f"photo {photo_id} : état {current.state}, suppression interdite")
            c.execute(
                "UPDATE photos SET delete_state = 'delete_requested', updated_at = ? WHERE id = ?",
                (utc_now(), photo_id),
            )
            self._log(c, "delete_requested", photo_id)

    def cancel_delete(self, photo_id: int) -> None:
        with self.transaction() as c:
            c.execute(
                "UPDATE photos SET delete_state = 'none', updated_at = ? "
                "WHERE id = ? AND delete_state = 'delete_requested'",
                (utc_now(), photo_id),
            )
            self._log(c, "delete_cancelled", photo_id)

    def mark_deleted_on_camera(self, photo_id: int) -> None:
        """À appeler seulement APRÈS une suppression PTP réussie, confirmée par l'utilisateur."""
        with self.transaction() as c:
            cur = c.execute(
                "UPDATE photos SET delete_state = 'deleted_on_camera', updated_at = ? "
                "WHERE id = ? AND delete_state = 'delete_requested'",
                (utc_now(), photo_id),
            )
            if cur.rowcount != 1:
                raise InvalidTransition(f"photo {photo_id} : suppression non demandée")
            self._log(c, "deleted_on_camera", photo_id)


def _split_sql(script: str) -> list[str]:
    """Découpe le script en instructions complètes (gère les BEGIN…END des triggers)."""
    statements, buf = [], ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                statements.append(buf.strip())
            buf = ""
    if buf.strip() and not all(l.strip().startswith("--") or not l.strip() for l in buf.splitlines()):
        raise ValueError("instruction SQL incomplète à la fin du schéma")
    return statements
