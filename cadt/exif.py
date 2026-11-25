"""Lecture des métadonnées avec exiftool, en processus permanent (-stay_open).

Lancer exiftool (Perl) coûte plusieurs dixièmes de seconde, bien plus sur le téléphone :
un seul processus sert toutes les photos. S'il meurt, il est relancé au prochain appel.

Les aperçus intégrés aux NEF (PreviewImage 640 px, JpgFromRaw pleine taille) ne passent pas
par exiftool : il donne leur position et leur longueur dans le fichier, et on les lit
directement (voir read_embedded).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

READY = "{ready}"

# « # » : valeur numérique brute (ex. 0.00625 au lieu de « 1/160 »).
TAGS = [
    "DateTimeOriginal", "SubSecTimeOriginal", "OffsetTimeOriginal",
    "Make", "Model", "LensID", "SerialNumber",
    "FocalLength#", "FNumber#", "ExposureTime#", "ISO#",
    "ImageWidth#", "ImageHeight#", "ImageSize#", "Orientation#", "Rating#", "ShutterCount#",
    "GPSLatitude#", "GPSLongitude#",
    "PreviewImageStart#", "PreviewImageLength#", "JpgFromRawStart#", "JpgFromRawLength#",
]


class ExifToolError(Exception):
    pass


def available() -> bool:
    return shutil.which("exiftool") is not None


class ExifTool:
    def __init__(self, executable: str = "exiftool", timeout: float = 60.0):
        self.executable = executable
        self.timeout = timeout
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def _start(self) -> subprocess.Popen[str]:
        if self._proc is None or self._proc.poll() is not None:
            self._proc = subprocess.Popen(
                [self.executable, "-stay_open", "True", "-@", "-"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", bufsize=1,
            )
        return self._proc

    def execute(self, *args: str) -> str:
        """Envoie une commande au processus permanent et renvoie sa sortie."""
        with self._lock:
            proc = self._start()
            try:
                proc.stdin.write("\n".join(args) + "\n-execute\n")
                proc.stdin.flush()
                lines = []
                while True:
                    line = proc.stdout.readline()
                    if line == "":
                        raise ExifToolError("exiftool s'est arrêté")
                    if line.rstrip("\n") == READY:
                        return "".join(lines)
                    lines.append(line)
            except (BrokenPipeError, ExifToolError):
                self._proc = None
                raise ExifToolError("exiftool s'est arrêté pendant la commande")

    def read(self, path: str | Path) -> dict[str, Any]:
        out = self.execute("-json", "-charset", "filename=utf8", *(f"-{t}" for t in TAGS), str(path))
        try:
            data = json.loads(out)
        except json.JSONDecodeError as e:
            raise ExifToolError(f"sortie illisible pour {path}") from e
        if not data:
            raise ExifToolError(f"aucune métadonnée pour {path}")
        return data[0]

    def close(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                try:
                    self._proc.stdin.write("-stay_open\nFalse\n")
                    self._proc.stdin.flush()
                    self._proc.wait(timeout=5)
                except (BrokenPipeError, subprocess.TimeoutExpired):
                    self._proc.kill()
            self._proc = None

    def __enter__(self) -> "ExifTool":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_embedded(path: str | Path, meta: dict[str, Any], name: str) -> bytes | None:
    """Aperçu intégré au fichier (name : « PreviewImage » ou « JpgFromRaw »), ou None."""
    start, length = meta.get(f"{name}Start"), meta.get(f"{name}Length")
    if not isinstance(start, int) or not isinstance(length, int) or length <= 0:
        return None
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read(length)
    return data if data[:2] == b"\xff\xd8" else None  # un JPEG commence par FF D8


# ------------------------------------------------------------------ mise en forme

@dataclass(frozen=True)
class Shooting:
    """Champs utiles à l'interface, extraits de la sortie d'exiftool."""
    taken_at: str | None
    make: str | None
    model: str | None
    lens: str | None
    serial: str | None
    focal_length: float | None
    aperture: float | None
    exposure_time: float | None
    iso: int | None
    width: int | None
    height: int | None
    orientation: int | None
    camera_rating: int | None
    shutter_count: int | None
    gps_lat: float | None
    gps_lon: float | None


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def _int(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) else None


def _str(v: Any) -> str | None:
    return str(v).strip() or None if v is not None else None


def parse(meta: dict[str, Any]) -> Shooting:
    taken = _str(meta.get("DateTimeOriginal"))
    if taken and len(taken) >= 19:
        taken = taken[:10].replace(":", "-") + "T" + taken[11:19]
        sub = _str(meta.get("SubSecTimeOriginal"))
        if sub and str(sub).isdigit():
            taken += f".{sub}"
        offset = _str(meta.get("OffsetTimeOriginal"))
        if offset:
            taken += offset
    width, height = _int(meta.get("ImageWidth")), _int(meta.get("ImageHeight"))
    size = meta.get("ImageSize")
    if isinstance(size, str) and len(size.split()) == 2:  # « 8288 5520 » (composite, prioritaire)
        width, height = (int(x) for x in size.split())
    return Shooting(
        taken_at=taken,
        make=_str(meta.get("Make")),
        model=_str(meta.get("Model")),
        lens=_str(meta.get("LensID")),
        serial=_str(meta.get("SerialNumber")),
        focal_length=_num(meta.get("FocalLength")),
        aperture=_num(meta.get("FNumber")),
        exposure_time=_num(meta.get("ExposureTime")),
        iso=_int(meta.get("ISO")),
        width=width,
        height=height,
        orientation=_int(meta.get("Orientation")),
        camera_rating=_int(meta.get("Rating")),
        shutter_count=_int(meta.get("ShutterCount")),
        gps_lat=_num(meta.get("GPSLatitude")),
        gps_lon=_num(meta.get("GPSLongitude")),
    )


def format_exposure(seconds: float | None) -> str | None:
    """0.00625 → « 1/160 s » ; 2.5 → « 2.5 s »."""
    if not seconds:
        return None
    if seconds >= 0.3:
        return f"{seconds:g} s"
    return f"1/{round(1 / seconds)} s"


def format_summary(s: Shooting) -> str:
    """« 35 mm · f/9 · 1/160 s · ISO 64 »"""
    parts = []
    if s.focal_length:
        parts.append(f"{s.focal_length:g} mm")
    if s.aperture:
        parts.append(f"f/{s.aperture:g}")
    if (e := format_exposure(s.exposure_time)):
        parts.append(e)
    if s.iso:
        parts.append(f"ISO {s.iso}")
    return " · ".join(parts)
