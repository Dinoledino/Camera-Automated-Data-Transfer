"""Source « appareil photo » via libgphoto2 (bindings python-gphoto2).

Sert au watcher (lister les fichiers) et au downloader (lire un fichier).
Fonctionne avec un vrai appareil PTP et avec le pilote « Directory Browse »
(port disk:<dossier>), utilisé avec le simulateur.

Points connus :
- Directory Browse ne prend pas en charge file_read (lecture par morceaux) : on se
  rabat sur file_get (fichier entier en mémoire, ~50 Mo pour un NEF de D850).
  Lecture partielle sur le D850 : À VÉRIFIER.
- libgphoto2 garde la liste des fichiers en cache : refresh() se reconnecte
  (exit puis init). Sur un vrai appareil, le cache est peut-être tenu à jour par les
  événements PTP, auquel cas la reconnexion serait inutile : À VÉRIFIER.
- Le D850 peut répondre « PTP Device Busy » (écriture sur la carte, mise au point) :
  erreur passagère (SourceBusy), qui ne pénalise pas la photo.
- Heure de prise de vue = mtime renvoyé par libgphoto2, interprété en heure locale.
  Exact avec Directory Browse ; pour un appareil PTP (date sans fuseau) : À VÉRIFIER.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterator

import gphoto2 as gp

from .camera import CameraFile, storage_of
from .downloader import CHUNK_SIZE, SourceBusy, SourceDisconnected, SourceError

__all__ = ["CameraFile", "Gphoto2Source", "autodetect", "DIRECTORY_BROWSE"]

DIRECTORY_BROWSE = "Directory Browse"

# Codes d'erreur qui signifient « l'appareil n'est plus là ».
_DISCONNECTED = {
    gp.GP_ERROR_IO, gp.GP_ERROR_IO_INIT, gp.GP_ERROR_IO_READ, gp.GP_ERROR_IO_WRITE,
    gp.GP_ERROR_IO_USB_FIND, gp.GP_ERROR_IO_USB_CLAIM, gp.GP_ERROR_IO_LOCK,
    gp.GP_ERROR_MODEL_NOT_FOUND, gp.GP_ERROR_TIMEOUT,
}


def autodetect() -> list[tuple[str, str]]:
    """Appareils USB reconnus par libgphoto2 : [(modèle, port)]."""
    found = gp.Camera.autodetect()
    return [(found.get_name(i), found.get_value(i)) for i in range(len(found))]


def _translate(e: gp.GPhoto2Error, what: str) -> SourceError:
    if e.code == gp.GP_ERROR_CAMERA_BUSY:
        return SourceBusy(f"{what} : appareil occupé")
    if e.code in _DISCONNECTED:
        return SourceDisconnected(f"{what} : {e}")
    return SourceError(f"{what} : {e}")


class Gphoto2Source:
    """Un appareil photo, détecté automatiquement ou désigné par modèle et port."""

    def __init__(self, model: str | None = None, port: str | None = None):
        self.model = model
        self.port = port
        self.camera: gp.Camera | None = None
        self._partial_reads = True   # passe à False si l'appareil refuse file_read

    @classmethod
    def directory(cls, card: str) -> "Gphoto2Source":
        """Un dossier vu comme un appareil (pilote Directory Browse) : pour le simulateur."""
        return cls(DIRECTORY_BROWSE, f"disk:{card}")

    # ---------------------------------------------------------- connexion

    def connect(self) -> None:
        camera = gp.Camera()
        try:
            if self.model:
                abilities = gp.CameraAbilitiesList()
                abilities.load()
                camera.set_abilities(abilities.get_abilities(abilities.lookup_model(self.model)))
            if self.port:
                ports = gp.PortInfoList()
                ports.load()
                camera.set_port_info(ports[ports.lookup_path(self.port)])
            camera.init()
        except gp.GPhoto2Error as e:
            # Après un débranchement ou un redémarrage de l'appareil, le port USB change
            # souvent (usb:001,008 → usb:001,009) : on cherche le même modèle ailleurs.
            moved = self._find_moved_port()
            if moved is None:
                raise SourceDisconnected(f"connexion impossible : {e}") from e
            self.port = moved
            return self.connect()
        self.camera = camera

    def _find_moved_port(self) -> str | None:
        if not self.model or not self.port or not self.port.startswith("usb:"):
            return None
        for model, port in autodetect():
            if model == self.model and port != self.port:
                return port
        return None

    def close(self) -> None:
        if self.camera is not None:
            try:
                self.camera.exit()
            except gp.GPhoto2Error:
                pass
            self.camera = None

    def refresh(self) -> None:
        """Vide le cache de libgphoto2 en se reconnectant."""
        self.close()
        self.connect()

    def _cam(self) -> gp.Camera:
        if self.camera is None:
            self.connect()
        return self.camera

    def __enter__(self) -> "Gphoto2Source":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------- liste

    def list_files(self, folder: str = "/") -> list[CameraFile]:
        """Tous les fichiers de l'appareil, en parcourant l'arborescence (dossiers cachés exclus)."""
        cam = self._cam()
        out: list[CameraFile] = []
        try:
            self._walk(cam, folder, out)
        except gp.GPhoto2Error as e:
            raise _translate(e, "liste des fichiers") from e
        return out

    def _walk(self, cam: gp.Camera, folder: str, out: list[CameraFile]) -> None:
        for i in range(len(files := cam.folder_list_files(folder))):
            name = files.get_name(i)
            info = cam.file_get_info(folder, name)
            out.append(CameraFile(
                folder=folder,
                name=name,
                size=int(info.file.size),
                capture_time=datetime.fromtimestamp(info.file.mtime).isoformat(timespec="seconds"),
                storage_id=storage_of(folder),
            ))
        for i in range(len(subs := cam.folder_list_folders(folder))):
            sub = subs.get_name(i)
            if not sub.startswith("."):
                self._walk(cam, f"{folder.rstrip('/')}/{sub}", out)

    # ---------------------------------------------------------- un fichier

    def stat(self, folder: str, name: str) -> CameraFile | None:
        """Informations actuelles du fichier sur l'appareil, ou None s'il n'existe plus."""
        try:
            info = self._cam().file_get_info(folder, name)
        except gp.GPhoto2Error as e:
            if e.code in (gp.GP_ERROR_FILE_NOT_FOUND, gp.GP_ERROR_DIRECTORY_NOT_FOUND):
                return None
            raise _translate(e, f"{folder}/{name}") from e
        return CameraFile(folder, name, int(info.file.size),
                          datetime.fromtimestamp(info.file.mtime).isoformat(timespec="seconds"),
                          storage_of(folder))

    def delete(self, folder: str, name: str) -> None:
        """Efface le fichier de la carte de l'appareil (PTP DeleteObject)."""
        try:
            self._cam().file_delete(folder, name)
        except gp.GPhoto2Error as e:
            raise _translate(e, f"effacement de {folder}/{name}") from e

    # ---------------------------------------------------------- lecture

    def iter_chunks(self, folder: str, name: str) -> Iterator[bytes]:
        cam = self._cam()
        what = f"{folder}/{name}"
        if self._partial_reads:
            buf = bytearray(CHUNK_SIZE)
            offset = 0
            try:
                while True:
                    n = cam.file_read(folder, name, gp.GP_FILE_TYPE_NORMAL, offset, buf)
                    if n <= 0:
                        return
                    yield bytes(buf[:n])
                    offset += n
            except gp.GPhoto2Error as e:
                if e.code != gp.GP_ERROR_NOT_SUPPORTED or offset:
                    raise _translate(e, what) from e
                self._partial_reads = False  # on se rabat sur file_get, désormais
        try:
            f = cam.file_get(folder, name, gp.GP_FILE_TYPE_NORMAL)
            data = memoryview(f.get_data_and_size())
        except gp.GPhoto2Error as e:
            raise _translate(e, what) from e
        for i in range(0, len(data), CHUNK_SIZE):
            yield bytes(data[i:i + CHUNK_SIZE])

