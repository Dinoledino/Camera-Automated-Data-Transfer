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
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA_VERSION = 6
_SCHEMA_FILE = Path(__file__).with_name("schema.sql")  # version 1
_MIGRATIONS_DIR = Path(__file__).with_name("migrations")  # NNN_<nom>.sql → version NNN

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
    session_id: int | None = None
    session_number: int | None = None
    next_attempt_at: str | None = None
    given_up_at: str | None = None
    priority: int = 0
    trashed_at: str | None = None
    trash_origin: str | None = None
    trash_boot: str | None = None
    purged_at: str | None = None
    favorite: int = 0

    @property
    def in_trash(self) -> bool:
        return self.trashed_at is not None and self.purged_at is None

    @property
    def is_saved(self) -> bool:
        return self.state in SAVED_STATES


@dataclass(frozen=True)
class Session:
    id: int
    name: str
    slug: str
    day: str
    folder: str
    next_number: int
    created_at: str


@dataclass(frozen=True)
class PendingMove:
    photo_id: int
    src: str
    dst: str
    session_id: int | None
    session_number: int | None
    created_at: str


def slugify(name: str) -> str:
    """« Mariage Dupont (église) » → « Mariage-Dupont-eglise » : sûr pour FAT/exFAT et les URL."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_name).strip("-")
    return slug[:60].rstrip("-") or "session"


def utc_now() -> str:
    return iso_utc(datetime.now(timezone.utc))


def iso_utc(moment: datetime) -> str:
    """Format unique des horodatages UTC : comparable directement comme texte en SQL."""
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")


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
        steps: list[tuple[int, Path]] = []
        if version == 0:
            steps.append((1, _SCHEMA_FILE))
        for f in sorted(_MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
            n = int(f.name[:3])
            if max(version, 1) < n <= SCHEMA_VERSION:
                steps.append((n, f))
        if not steps or steps[-1][0] != SCHEMA_VERSION:
            raise RuntimeError(f"migration manquante vers la version {SCHEMA_VERSION}")
        # Une seule transaction : soit la base passe entièrement en nouvelle version, soit rien.
        with self.transaction() as c:
            for n, f in steps:
                for statement in _split_sql(f.read_text(encoding="utf-8")):
                    c.execute(statement)
                c.execute(f"PRAGMA user_version = {n}")

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
                session_id = self._active_session_id(c)
                if session_id is not None:
                    photo = self._attach(c, photo, session_id)
                self._log(c, "detected", photo.id, f"{camera_folder}/{name}",
                          data={"session_id": session_id} if session_id else None)
                return photo, True
            row = c.execute(
                "SELECT * FROM photos WHERE name = ? AND size = ? AND capture_time = ?",
                (name, size, capture_time),
            ).fetchone()
            return Photo(**dict(row)), False

    # ------------------------------------------------------------- file

    def claim_next(self, now: str | None = None) -> Photo | None:
        """Prend atomiquement la prochaine photo à télécharger.

        Ordre : priorité manuelle, puis JPEG > RAW > autres, puis ordre chronologique.
        Sont ignorées : les photos abandonnées, et celles dont le délai avant
        nouvelle tentative (next_attempt_at) n'est pas écoulé.
        """
        now = now or utc_now()
        with self.transaction() as c:
            row = c.execute(
                """UPDATE photos
                   SET state = 'downloading', attempts = attempts + 1,
                       next_attempt_at = NULL, updated_at = ?
                   WHERE id = (
                       SELECT id FROM photos
                       WHERE state = 'detected' AND given_up_at IS NULL
                         AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                       ORDER BY priority DESC,
                                CASE kind WHEN 'jpeg' THEN 0 WHEN 'raw' THEN 1 ELSE 2 END,
                                capture_time, id
                       LIMIT 1)
                   RETURNING *""",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            photo = Photo(**dict(row))
            self._log(c, "download_started", photo.id, data={"attempt": photo.attempts})
            return photo

    def next_retry_at(self) -> str | None:
        """Heure de la prochaine tentative programmée (pour savoir combien de temps dormir)."""
        return self.conn.execute(
            "SELECT min(next_attempt_at) FROM photos "
            "WHERE state = 'detected' AND given_up_at IS NULL AND next_attempt_at IS NOT NULL"
        ).fetchone()[0]

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

    def mark_failed(self, photo_id: int, error: str, retry_at: str | None = None,
                    give_up: bool = False, count_attempt: bool = True) -> Photo:
        """Échec pendant le téléchargement : la photo retourne dans la file.

        retry_at      : pas de nouvelle tentative avant cette heure (None : tout de suite).
        give_up       : la photo sort de la file (reste non sauvegardée, relance manuelle).
        count_attempt : False quand l'échec ne vient pas de la photo (appareil débranché,
                        carte pleine) : la tentative n'est pas comptée.
        """
        with self.transaction() as c:
            photo = self._transition(
                c, photo_id, ("downloading", "downloaded"),
                "state = 'detected', sha256 = NULL, last_error = ?, next_attempt_at = ?, "
                "attempts = attempts - ?",
                (error, retry_at, 0 if count_attempt else 1))
            self._log(c, "error", photo_id, error,
                      data={"retry_at": retry_at, "counted": count_attempt})
            if give_up:
                photo = self._give_up(c, photo)
            return photo

    def schedule_retry(self, photo_id: int, retry_at: str | None, give_up: bool = False) -> Photo:
        """Applique la politique de nouvelle tentative à une photo déjà revenue en « detected »
        (par exemple après un hash différent à la relecture)."""
        with self.transaction() as c:
            photo = self._transition(c, photo_id, ("detected",), "next_attempt_at = ?", (retry_at,))
            if give_up:
                photo = self._give_up(c, photo)
            return photo

    def _give_up(self, c: sqlite3.Connection, photo: Photo) -> Photo:
        row = c.execute(
            "UPDATE photos SET given_up_at = ?, next_attempt_at = NULL WHERE id = ? RETURNING *",
            (utc_now(), photo.id),
        ).fetchone()
        self._log(c, "given_up", photo.id, data={"attempts": photo.attempts,
                                                 "last_error": photo.last_error})
        return Photo(**dict(row))

    def retry(self, photo_ids: Iterable[int] | None = None) -> int:
        """Relance manuelle des photos abandonnées (toutes si photo_ids est None).

        Leur compteur de tentatives repart de zéro. Renvoie le nombre de photos relancées.
        """
        with self.transaction() as c:
            sql = ("UPDATE photos SET given_up_at = NULL, next_attempt_at = NULL, attempts = 0, "
                   "updated_at = ? WHERE state = 'detected' AND given_up_at IS NOT NULL")
            params: list[Any] = [utc_now()]
            if photo_ids is not None:
                ids = list(photo_ids)
                sql += f" AND id IN ({','.join('?' * len(ids))})" if ids else " AND 0"
                params += ids
            rows = c.execute(sql + " RETURNING id", params).fetchall()
            for r in rows:
                self._log(c, "retry_requested", r["id"])
            return len(rows)

    def set_priority(self, photo_ids: Iterable[int], priority: int = 1) -> None:
        """Ex. « télécharger celle-ci maintenant » : priority > 0 passe devant le reste.

        Une photo prioritaire en attente de nouvelle tentative est reprise tout de suite.
        """
        ids = list(photo_ids)
        if not ids:
            return
        with self.transaction() as c:
            c.execute(
                f"UPDATE photos SET priority = ?, "
                f"next_attempt_at = CASE WHEN ? > 0 THEN NULL ELSE next_attempt_at END, "
                f"updated_at = ? WHERE id IN ({','.join('?' * len(ids))})",
                (priority, priority, utc_now(), *ids),
            )
            for i in ids:
                self._log(c, "priority", i, data={"priority": priority})

    def count_given_up(self) -> int:
        return self.conn.execute(
            "SELECT count(*) FROM photos WHERE state = 'detected' AND given_up_at IS NOT NULL"
        ).fetchone()[0]

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

    # ------------------------------------------------------------- sessions

    def create_session(self, name: str, day: str | None = None) -> Session:
        """Crée une session. day : date locale AAAA-MM-JJ (aujourd'hui par défaut)."""
        name = name.strip()
        if not name:
            raise ValueError("nom de session vide")
        day = day or date.today().isoformat()
        slug = slugify(name)
        with self.transaction() as c:
            folder, n = f"{day}_{slug}", 2
            while c.execute("SELECT 1 FROM sessions WHERE folder = ?", (folder,)).fetchone():
                folder, n = f"{day}_{slug}-{n}", n + 1
            row = c.execute(
                "INSERT INTO sessions (name, slug, day, folder, created_at) "
                "VALUES (?, ?, ?, ?, ?) RETURNING *",
                (name, folder[len(day) + 1:], day, folder, utc_now()),
            ).fetchone()
            session = Session(**dict(row))
            self._log(c, "session_created", data={"session_id": session.id, "folder": folder})
            return session

    def get_session(self, session_id: int) -> Session | None:
        row = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return Session(**dict(row)) if row else None

    def list_sessions(self) -> list[Session]:
        rows = self.conn.execute("SELECT * FROM sessions ORDER BY day DESC, id DESC").fetchall()
        return [Session(**dict(r)) for r in rows]

    def _active_session_id(self, c: sqlite3.Connection) -> int | None:
        row = c.execute("SELECT value FROM app_state WHERE key = 'active_session_id'").fetchone()
        return int(row["value"]) if row and row["value"] is not None else None

    def active_session(self) -> Session | None:
        session_id = self._active_session_id(self.conn)
        return self.get_session(session_id) if session_id is not None else None

    def set_active_session(self, session_id: int | None) -> None:
        """Les photos détectées à partir de maintenant iront dans cette session (None : aucune)."""
        with self.transaction() as c:
            if session_id is not None and not c.execute(
                    "SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone():
                raise ValueError(f"session {session_id} introuvable")
            c.execute("INSERT INTO app_state (key, value) VALUES ('active_session_id', ?) "
                      "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (session_id,))
            self._log(c, "active_session", data={"session_id": session_id})

    def _number_for(self, c: sqlite3.Connection, session_id: int, group_key: str,
                    exclude_photo: int | None = None) -> int:
        """Numéro dans la session : celui de la photo jumelle (RAW/JPEG) s'il existe, sinon le suivant."""
        twin = c.execute(
            "SELECT session_number FROM photos WHERE session_id = ? AND group_key = ? "
            "AND session_number IS NOT NULL AND id IS NOT ?",
            (session_id, group_key, exclude_photo),
        ).fetchone()
        if twin is not None:
            return twin["session_number"]
        return c.execute(
            "UPDATE sessions SET next_number = next_number + 1 WHERE id = ? "
            "RETURNING next_number - 1", (session_id,),
        ).fetchone()[0]

    def _attach(self, c: sqlite3.Connection, photo: Photo, session_id: int) -> Photo:
        number = self._number_for(c, session_id, photo.group_key, photo.id)
        row = c.execute(
            "UPDATE photos SET session_id = ?, session_number = ?, updated_at = ? "
            "WHERE id = ? RETURNING *",
            (session_id, number, utc_now(), photo.id),
        ).fetchone()
        return Photo(**dict(row))

    def assign_session(self, photo_id: int, session_id: int | None) -> Photo:
        """Change la session d'une photo pas encore téléchargée (état « detected »).

        Pour une photo sauvegardée, il faut déplacer le fichier : voir cadt.sessions.move_photos.
        """
        with self.transaction() as c:
            row = c.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
            if row is None or row["state"] != "detected":
                got = row["state"] if row else "introuvable"
                raise InvalidTransition(f"photo {photo_id} : état {got}, attendu ('detected',)")
            photo = Photo(**dict(row))
            if session_id is None:
                photo = Photo(**dict(c.execute(
                    "UPDATE photos SET session_id = NULL, session_number = NULL, updated_at = ? "
                    "WHERE id = ? RETURNING *", (utc_now(), photo_id)).fetchone()))
            elif session_id != photo.session_id:
                photo = self._attach(c, photo, session_id)
            self._log(c, "session_assigned", photo_id, data={"session_id": session_id})
            return photo

    def next_number_in(self, session_id: int, photo: Photo) -> int:
        """Numéro qu'aurait la photo dans cette session (réserve un numéro si besoin)."""
        with self.transaction() as c:
            if photo.session_id == session_id and photo.session_number is not None:
                return photo.session_number
            return self._number_for(c, session_id, photo.group_key, photo.id)

    # ------------------------------------------------------------- déplacements

    def begin_move(self, photo_id: int, dst: str, session_id: int | None,
                   session_number: int | None) -> PendingMove:
        """Étape 1 du déplacement : enregistre l'intention, AVANT de toucher au fichier."""
        with self.transaction() as c:
            row = c.execute("SELECT state, local_path FROM photos WHERE id = ?", (photo_id,)).fetchone()
            if row is None or row["state"] not in SAVED_STATES:
                got = row["state"] if row else "introuvable"
                raise InvalidTransition(f"photo {photo_id} : état {got}, déplacement réservé aux photos sauvegardées")
            if c.execute("SELECT 1 FROM pending_moves WHERE photo_id = ?", (photo_id,)).fetchone():
                raise InvalidTransition(f"photo {photo_id} : déplacement déjà en cours")
            r = c.execute(
                "INSERT INTO pending_moves (photo_id, src, dst, session_id, session_number, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) RETURNING *",
                (photo_id, row["local_path"], dst, session_id, session_number, utc_now()),
            ).fetchone()
            return PendingMove(**dict(r))

    def finish_move(self, photo_id: int) -> Photo:
        """Étape 3 : le fichier est à sa nouvelle place ; la base suit."""
        with self.transaction() as c:
            m = c.execute("SELECT * FROM pending_moves WHERE photo_id = ?", (photo_id,)).fetchone()
            if m is None:
                raise InvalidTransition(f"photo {photo_id} : aucun déplacement en cours")
            row = c.execute(
                "UPDATE photos SET local_path = ?, session_id = ?, session_number = ?, updated_at = ? "
                "WHERE id = ? RETURNING *",
                (m["dst"], m["session_id"], m["session_number"], utc_now(), photo_id),
            ).fetchone()
            c.execute("DELETE FROM pending_moves WHERE photo_id = ?", (photo_id,))
            self._log(c, "moved", photo_id, data={"from": m["src"], "to": m["dst"],
                                                   "session_id": m["session_id"]})
            return Photo(**dict(row))

    def cancel_move(self, photo_id: int, reason: str) -> None:
        with self.transaction() as c:
            c.execute("DELETE FROM pending_moves WHERE photo_id = ?", (photo_id,))
            self._log(c, "move_cancelled", photo_id, reason)

    def pending_moves(self) -> list[PendingMove]:
        return [PendingMove(**dict(r))
                for r in self.conn.execute("SELECT * FROM pending_moves ORDER BY photo_id")]

    def path_owner(self, path: str, exclude_photo: int | None = None) -> int | None:
        """Id de la photo qui occupe (ou va occuper) ce chemin, s'il y en a une."""
        row = self.conn.execute(
            "SELECT id FROM photos WHERE local_path = ? AND id IS NOT ? "
            "UNION SELECT photo_id FROM pending_moves WHERE dst = ? AND photo_id IS NOT ? LIMIT 1",
            (path, exclude_photo, path, exclude_photo),
        ).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------- tri

    def set_rating(self, photo_id: int, rating: int) -> None:
        with self.transaction() as c:
            c.execute("UPDATE photos SET rating = ?, updated_at = ? WHERE id = ?",
                      (rating, utc_now(), photo_id))
            self._log(c, "rating", photo_id, data={"rating": rating})

    def set_favorite(self, photo_id: int, favorite: bool) -> None:
        with self.transaction() as c:
            c.execute("UPDATE photos SET favorite = ?, updated_at = ? WHERE id = ?",
                      (1 if favorite else 0, utc_now(), photo_id))
            self._log(c, "favorite", photo_id, data={"favorite": favorite})

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

    def mark_deleted_on_camera(self, photo_id: int, note: str | None = None) -> None:
        """À appeler seulement APRÈS l'effacement sur l'appareil (ou le constat de son absence)."""
        with self.transaction() as c:
            cur = c.execute(
                "UPDATE photos SET delete_state = 'deleted_on_camera', updated_at = ? "
                "WHERE id = ? AND delete_state = 'delete_requested'",
                (utc_now(), photo_id),
            )
            if cur.rowcount != 1:
                raise InvalidTransition(f"photo {photo_id} : suppression non demandée")
            self._log(c, "deleted_on_camera", photo_id, note)

    # ------------------------------------------------------------- corbeille

    def mark_trashed(self, photo_id: int, origin: str, boot_id: str | None, at: str | None = None) -> Photo:
        """at : heure de mise à la corbeille (celle de l'horloge qui calcule le délai)."""
        with self.transaction() as c:
            row = c.execute(
                "UPDATE photos SET trashed_at = ?, trash_origin = ?, trash_boot = ?, updated_at = ? "
                "WHERE id = ? AND purged_at IS NULL RETURNING *",
                (at or utc_now(), origin, boot_id, utc_now(), photo_id)).fetchone()
            if row is None:
                raise InvalidTransition(f"photo {photo_id} : déjà supprimée")
            self._log(c, "trashed", photo_id, origin)
            return Photo(**dict(row))

    def mark_restored(self, photo_id: int) -> Photo:
        with self.transaction() as c:
            row = c.execute(
                "UPDATE photos SET trashed_at = NULL, trash_origin = NULL, trash_boot = NULL, "
                "flag = CASE flag WHEN 'reject' THEN 'none' ELSE flag END, updated_at = ? "
                "WHERE id = ? AND purged_at IS NULL RETURNING *", (utc_now(), photo_id)).fetchone()
            if row is None:
                raise InvalidTransition(f"photo {photo_id} : déjà supprimée")
            self._log(c, "restored", photo_id)
            return Photo(**dict(row))

    def mark_purged(self, photo_id: int, reason: str) -> None:
        with self.transaction() as c:
            c.execute("UPDATE photos SET purged_at = ?, updated_at = ? WHERE id = ? AND purged_at IS NULL",
                      (utc_now(), utc_now(), photo_id))
            self._log(c, "purged", photo_id, reason)

    def trashed_photos(self) -> list[Photo]:
        """Photos dans la corbeille, les plus anciennes d'abord."""
        rows = self.conn.execute("SELECT * FROM photos WHERE trashed_at IS NOT NULL AND purged_at IS NULL "
                                 "ORDER BY trashed_at, id").fetchall()
        return [Photo(**dict(r)) for r in rows]

    # ------------------------------------------------------------- réglages

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row and row["value"] is not None else default

    def set_setting(self, key: str, value: str | None) -> None:
        with self.transaction() as c:
            c.execute("INSERT INTO app_state (key, value) VALUES (?, ?) "
                      "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))
            self._log(c, "setting", data={"key": key, "value": value})

    # ------------------------------------------------------------- effacement sur l'appareil

    def auto_delete_enabled(self) -> bool:
        """Effacement automatique de la carte de l'appareil après vérification (activé par défaut)."""
        return self.get_setting("auto_delete_camera", "1") == "1"

    def set_auto_delete(self, enabled: bool) -> None:
        """Le réactiver ne concerne que les photos vérifiées à partir de maintenant : celles d'une
        séance faite avec l'effacement désactivé restent sur l'appareil."""
        self.set_setting("auto_delete_camera", "1" if enabled else "0")
        if enabled:
            self.set_setting("auto_delete_since", utc_now())

    def camera_deletion_candidates(self, limit: int = 200) -> list[Photo]:
        """Photos vérifiées encore sur l'appareil (ou dont l'effacement a été interrompu)."""
        since = self.get_setting("auto_delete_since", "")
        rows = self.conn.execute(
            """SELECT * FROM photos
               WHERE state IN ('verified', 'synced_pc')
                 AND delete_state IN ('none', 'delete_requested')
                 AND purged_at IS NULL  -- supprimée du téléphone : l'appareil garde la dernière copie
                 AND (delete_state = 'delete_requested' OR verified_at >= ?)
               ORDER BY verified_at, id LIMIT ?""", (since, limit)).fetchall()
        return [Photo(**dict(r)) for r in rows]


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
