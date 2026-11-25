"""Sessions de prise de vue : emplacement des fichiers et déplacements entre sessions.

- Photo dans une session : <racine>/<day>_<slug>/<day>_<slug>_<NNNN>.<ext>
- Photo sans session     : <racine>/<date de prise de vue>/<nom d'origine>

Un déplacement après coup garde le nom du fichier, sauf si rename=True : la photo prend
alors le nom correspondant à sa nouvelle session.

Déplacer un fichier sauvegardé se fait en trois temps (voir pending_moves dans la base) :
intention enregistrée → renommage sur le disque → base mise à jour. `recover_moves()`
termine ou annule, au démarrage, un déplacement interrompu par une coupure.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .db import Database, InvalidTransition, Photo, Session
from .fsutil import fsync_dir
from .storage import StoragePool

NO_SESSION_FOLDER_FALLBACK = "sans-date"


def session_file_name(session: Session, number: int, original_name: str) -> str:
    """2026-11-23_Mariage-Dupont_0001.NEF (l'extension d'origine est conservée)."""
    return f"{session.folder}_{number:04d}{Path(original_name).suffix}"


def dated_folder(photo: Photo) -> str:
    return photo.capture_time[:10] or NO_SESSION_FOLDER_FALLBACK


def free_path(db: Database, wanted: Path, photo_id: int) -> Path:
    """`wanted` s'il n'appartient à aucune autre photo, sinon <nom>_<id><ext>.

    On ne remplace jamais le fichier d'une autre photo. Un fichier présent sur le disque
    mais inconnu de la base est un reste de coupure de cette même photo : on peut l'écraser.
    """
    if db.path_owner(str(wanted), exclude_photo=photo_id) is None:
        return wanted
    return wanted.with_name(f"{wanted.stem}_{photo_id}{wanted.suffix}")


def download_path(db: Database, dest_root: Path, photo: Photo) -> Path:
    """Chemin final d'une photo qu'on va télécharger, selon sa session."""
    if photo.session_id is not None and photo.session_number is not None:
        session = db.get_session(photo.session_id)
        wanted = dest_root / session.folder / session_file_name(session, photo.session_number, photo.name)
    else:
        wanted = dest_root / dated_folder(photo) / photo.name
    return free_path(db, wanted, photo.id)


# ------------------------------------------------------------------ déplacements

@dataclass(frozen=True)
class MoveResult:
    moved: list[Photo]
    reassigned: list[Photo]          # pas encore téléchargées : seule la base change
    refused: list[tuple[int, str]]   # (id, raison)


def move_photos(db: Database, dest_root: "str | Path | StoragePool", photo_ids: Iterable[int],
                session_id: int | None, rename: bool = False) -> MoveResult:
    """Range des photos dans une autre session (None : retour au dossier par date).

    rename=False (par défaut) : le fichier garde son nom.
    rename=True : il prend le nom <day>_<slug>_<NNNN> de la nouvelle session
    (ou son nom d'origine, s'il retourne au dossier par date).
    """
    pool = StoragePool.of(dest_root)
    session = None
    if session_id is not None:
        session = db.get_session(session_id)
        if session is None:
            raise ValueError(f"session {session_id} introuvable")

    moved, reassigned, refused = [], [], []
    for photo_id in photo_ids:
        photo = db.get(photo_id)
        if photo is None:
            refused.append((photo_id, "introuvable"))
            continue
        if photo.trashed_at or photo.purged_at:
            refused.append((photo_id, "dans la corbeille ou supprimée"))
            continue
        if photo.state == "detected":
            reassigned.append(db.assign_session(photo_id, session_id))
            continue
        if not photo.is_saved:
            refused.append((photo_id, f"téléchargement en cours ({photo.state})"))
            continue
        if photo.session_id == session_id and not rename:
            continue
        try:
            # Même emplacement que le fichier actuel : le déplacement reste un renommage atomique.
            moved.append(_move_one(db, pool.root_of(photo.local_path), photo, session, rename))
        except InvalidTransition as e:
            refused.append((photo_id, str(e)))
    return MoveResult(moved, reassigned, refused)


def _move_one(db: Database, dest_root: Path, photo: Photo, session: Session | None,
              rename: bool) -> Photo:
    src = Path(photo.local_path)
    if session is not None:
        number = db.next_number_in(session.id, photo)
        folder = dest_root / session.folder
        name = session_file_name(session, number, photo.name) if rename else src.name
    else:
        number = None
        folder = dest_root / dated_folder(photo)
        name = photo.name if rename else src.name

    dst = free_path(db, folder / name, photo.id)
    if dst == src:
        db.begin_move(photo.id, str(dst), session.id if session else None, number)
        return db.finish_move(photo.id)

    db.begin_move(photo.id, str(dst), session.id if session else None, number)
    folder.mkdir(parents=True, exist_ok=True)
    fsync_dir(folder.parent)
    # os.replace est atomique sur un même système de fichiers : à tout instant, le fichier
    # est soit à src, soit à dst. Un fichier déjà à dst est un reste de coupure (free_path
    # garantit qu'il n'appartient à aucune autre photo).
    os.replace(src, dst)
    fsync_dir(dst.parent)
    if src.parent != dst.parent:
        fsync_dir(src.parent)
    return db.finish_move(photo.id)


def recover_moves(db: Database) -> list[int]:
    """À lancer au démarrage : termine ou annule les déplacements interrompus.

    Renvoie les ids des photos dont le fichier est introuvable aux deux endroits
    (anomalie grave, à signaler à l'utilisateur : rien n'est modifié pour elles).
    """
    lost = []
    for m in db.pending_moves():
        src_exists, dst_exists = os.path.exists(m.src), os.path.exists(m.dst)
        if not src_exists and dst_exists:
            db.finish_move(m.photo_id)
        elif src_exists:
            # Renommage pas fait (ou fait sur un reste de coupure) : on reste où on était.
            db.cancel_move(m.photo_id, "interrompu avant le renommage")
        else:
            db.log_event("move_file_missing", m.photo_id, f"{m.src} et {m.dst} absents")
            lost.append(m.photo_id)
    return lost
