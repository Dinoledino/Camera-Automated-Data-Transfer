"""Effacement automatique de la carte de l'appareil, après vérification (CLAUDE.md, règle 2).

Pour chaque photo vérifiée encore sur l'appareil :
1. la base note la demande (`delete_requested`) ;
2. pour chaque exemplaire sur l'appareil (deux en mode « Sauvegarde » du D850), on relit ses
   informations juste avant d'effacer : seul un fichier de même nom, même taille et même date
   que la copie vérifiée est effacé. Le D850 réutilise les noms après DSC_9999 : un fichier
   différent portant le même nom n'est jamais touché ;
3. la base note l'effacement (`deleted_on_camera`).

Après une coupure entre 2 et 3, le fichier a disparu de l'appareil : le passage suivant le
constate et termine l'étape 3. Une photo absente de l'appareil (carte retirée, effacée à la main,
nom réutilisé par une autre photo) est notée comme ne s'y trouvant plus, sans rien effacer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol

from .camera import CameraFile
from .db import Database, Photo
from .downloader import SourceBusy, SourceDisconnected, SourceError


class DeletableSource(Protocol):
    def stat(self, folder: str, name: str) -> CameraFile | None: ...
    def delete(self, folder: str, name: str) -> None: ...


@dataclass
class CleanResult:
    deleted: list[int] = field(default_factory=list)       # photos effacées de l'appareil
    already_absent: list[int] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)


class CameraCleaner:
    def __init__(self, db: Database, source: DeletableSource):
        self.db = db
        self.source = source

    def run(self, listing: Iterable[CameraFile] = ()) -> CleanResult:
        """Efface de l'appareil les photos vérifiées.

        listing : la dernière liste des fichiers de l'appareil (pour trouver tous les exemplaires
        d'une photo, sur les deux cartes). S'arrête sans erreur si l'appareil est occupé ; lève
        SourceDisconnected s'il n'est plus là.
        """
        result = CleanResult()
        if not self.db.auto_delete_enabled():
            return result
        by_key: dict[tuple, list[tuple[str, str]]] = {}
        for f in listing:
            by_key.setdefault(f.key, []).append((f.folder, f.name))
        for photo in self.db.camera_deletion_candidates():
            try:
                self._clean_one(photo, by_key, result)
            except SourceBusy:
                break  # on réessaiera au prochain passage
            except SourceDisconnected:
                raise
            except SourceError as e:
                result.errors.append((photo.id, str(e)))
                self.db.log_event("camera_delete_error", photo.id, str(e))
        return result

    def _clean_one(self, photo: Photo, by_key: dict, result: CleanResult) -> None:
        key = (photo.name, photo.size, photo.capture_time)
        places = list(dict.fromkeys(by_key.get(key, []) + [(photo.camera_folder, photo.name)]))
        self.db.request_delete(photo.id)
        deleted_any = False
        for folder, name in places:
            current = self.source.stat(folder, name)
            if current is None or current.key != key:
                continue  # plus là, ou autre photo portant le même nom : on n'y touche pas
            self.source.delete(folder, name)
            deleted_any = True
        if deleted_any:
            self.db.mark_deleted_on_camera(photo.id, f"{len(places)} emplacement(s) vérifié(s)")
            result.deleted.append(photo.id)
        else:
            self.db.mark_deleted_on_camera(photo.id, "déjà absente de l'appareil")
            result.already_absent.append(photo.id)
