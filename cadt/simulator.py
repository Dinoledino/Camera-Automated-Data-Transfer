"""Simulateur de D850 : écrit des photos sur une « carte » (un dossier) comme le ferait l'appareil.

À lire avec le pilote gphoto2 « Directory Browse » (port disk:<dossier>), ou directement.

Ce qui est imité :
- arborescence DCIM/100NC850, 101NC850… ; un nouveau dossier tous les 999 fichiers ;
- numérotation DSC_0001 → DSC_9999, puis retour à DSC_0001 dans un nouveau dossier ;
- RAW seul, ou paire RAW + JPEG sous le même numéro (« RAW principal, JPEG secondaire ») ;
- prise de vue à l'unité ou en rafale (7 images/s par défaut, la cadence du D850) ;
- un fichier n'apparaît que complet (écrit à côté, puis renommé) ; l'option
  `atomic=False` l'écrit au contraire progressivement, pour tester les lectures
  d'un fichier pas encore terminé ;
- date de modification = heure de prise de vue.

Constats faits avec Directory Browse (libgphoto2 2.5.34, 2026-11-25) :
- pas d'événement « nouvel objet » : seul le mode polling est testable avec ce pilote ;
- la liste des fichiers est mise en cache : il faut exit() puis init() pour voir les nouveaux ;
- file_read (lecture par morceaux) n'est pas pris en charge, file_get (fichier entier) oui.

Utilisation :
    python -m cadt.simulator CARTE --burst 10          # rafale de 10 s à 7 i/s
    python -m cadt.simulator CARTE --shots 5 --interval 2 --jpeg
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

MAX_NUMBER = 9999
MAX_PER_FOLDER = 999
FIRST_FOLDER = 100
LAST_FOLDER = 999
FOLDER_SUFFIX = "NC850"
_FOLDER_RE = re.compile(rf"^(\d{{3}}){FOLDER_SUFFIX}$")
_FILE_RE = re.compile(r"^DSC_(\d{4})\.(NEF|JPG)$", re.IGNORECASE)

# Tailles réalistes d'un D850 (RAW 14 bits compressé sans perte ; JPEG fine, taille L)
D850_RAW_SIZE = 50 * 1024 * 1024
D850_JPEG_SIZE = 20 * 1024 * 1024
TMP_DIR_NAME = ".sim-tmp"


@dataclass
class Shot:
    number: int
    folder: Path
    files: list[Path]
    capture_time: datetime


@dataclass
class SimulatedCamera:
    card: Path
    raw_size: int = D850_RAW_SIZE
    jpeg_size: int | None = None          # None : RAW seul
    atomic: bool = True
    clock: Callable[[], datetime] = datetime.now
    sleep: Callable[[float], None] = time.sleep
    _base_block: bytes = field(default=b"", repr=False)
    _folder: int = FIRST_FOLDER
    _number: int = 0
    _in_folder: int = 0

    def __post_init__(self) -> None:
        self.card = Path(self.card)
        self._base_block = os.urandom(1024 * 1024)
        self._resume_from_card()

    # ------------------------------------------------------------- état

    def _resume_from_card(self) -> None:
        """Reprend la numérotation là où la carte s'est arrêtée (comme l'appareil)."""
        dcim = self.card / "DCIM"
        if not dcim.is_dir():
            return
        folders = sorted(int(m.group(1)) for d in dcim.iterdir()
                         if d.is_dir() and (m := _FOLDER_RE.match(d.name)))
        if not folders:
            return
        self._folder = folders[-1]
        numbers = sorted({int(m.group(1)) for f in (dcim / f"{self._folder}{FOLDER_SUFFIX}").iterdir()
                          if (m := _FILE_RE.match(f.name))})
        self._in_folder = len(numbers)
        self._number = numbers[-1] if numbers else 0

    def _next_slot(self) -> tuple[int, Path]:
        number = self._number + 1
        new_folder = self._in_folder >= MAX_PER_FOLDER
        if number > MAX_NUMBER:
            number, new_folder = 1, True
        if new_folder:
            if self._folder >= LAST_FOLDER:
                raise RuntimeError("carte pleine : dossier 999 atteint (comme le vrai D850)")
            self._folder += 1
            self._in_folder = 0
        self._number = number
        self._in_folder += 1
        folder = self.card / "DCIM" / f"{self._folder}{FOLDER_SUFFIX}"
        folder.mkdir(parents=True, exist_ok=True)
        return number, folder

    # ------------------------------------------------------------- écriture

    def _content(self, name: str, size: int, stamp: datetime) -> bytes:
        """Contenu unique par fichier (en-tête aléatoire), rapide à produire pour 50 Mo."""
        header = f"SIM {name} {stamp.isoformat()} ".encode() + os.urandom(4096)
        body_len = max(size - len(header), 0)
        reps, rest = divmod(body_len, len(self._base_block))
        return (header + self._base_block * reps + self._base_block[:rest])[:size]

    def _write(self, path: Path, data: bytes, stamp: datetime) -> None:
        ts = stamp.timestamp()
        if self.atomic:
            tmp_dir = self.card / TMP_DIR_NAME
            tmp_dir.mkdir(exist_ok=True)
            tmp = tmp_dir / path.name
            tmp.write_bytes(data)
            os.utime(tmp, (ts, ts))
            os.replace(tmp, path)
        else:
            with open(path, "wb") as f:
                for i in range(0, len(data), 1024 * 1024):
                    f.write(data[i:i + 1024 * 1024])
                    f.flush()
            os.utime(path, (ts, ts))

    def shoot(self) -> Shot:
        number, folder = self._next_slot()
        stamp = self.clock().replace(microsecond=0)
        files = []
        for ext, size in (("NEF", self.raw_size), ("JPG", self.jpeg_size)):
            if size is None:
                continue
            path = folder / f"DSC_{number:04d}.{ext}"
            self._write(path, self._content(path.name, size, stamp), stamp)
            files.append(path)
        return Shot(number, folder, files, stamp)

    def burst(self, seconds: float, fps: float = 7.0) -> tuple[list[Shot], float]:
        """Rafale : renvoie (prises, retard max en secondes sur la cadence visée)."""
        shots, lag = [], 0.0
        period = 1.0 / fps
        start = time.monotonic()
        for i in range(round(seconds * fps)):
            target = start + i * period
            now = time.monotonic()
            if now < target:
                self.sleep(target - now)
            else:
                lag = max(lag, now - target)
            shots.append(self.shoot())
        return shots, lag


# ------------------------------------------------------------------ ligne de commande

def _size(text: str) -> int:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kKmMgG]?)[oOBb]?", text.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"taille invalide : {text}")
    mult = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[m.group(2).lower()]
    return int(float(m.group(1)) * mult)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Simule un D850 qui écrit sur sa carte.")
    p.add_argument("card", type=Path, help="dossier qui joue le rôle de la carte")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--burst", type=float, metavar="SECONDES", help="rafale de cette durée")
    mode.add_argument("--shots", type=int, help="nombre de prises à l'unité")
    p.add_argument("--fps", type=float, default=7.0, help="cadence de la rafale (7)")
    p.add_argument("--interval", type=float, default=1.0, help="secondes entre deux prises (--shots)")
    p.add_argument("--raw-size", type=_size, default=D850_RAW_SIZE, help="taille d'un NEF (50M)")
    p.add_argument("--jpeg", nargs="?", const=D850_JPEG_SIZE, type=_size, default=None,
                   metavar="TAILLE", help="ajoute un JPEG à chaque prise (20M par défaut)")
    p.add_argument("--slow-write", action="store_true",
                   help="écrit les fichiers progressivement au lieu de les faire apparaître complets")
    args = p.parse_args(argv)

    cam = SimulatedCamera(args.card, raw_size=args.raw_size, jpeg_size=args.jpeg,
                          atomic=not args.slow_write)
    t0 = time.monotonic()
    if args.burst is not None:
        shots, lag = cam.burst(args.burst, args.fps)
    else:
        shots, lag = [], 0.0
        for i in range(args.shots):
            if i:
                time.sleep(args.interval)
            shots.append(cam.shoot())
    elapsed = time.monotonic() - t0
    files = [f for s in shots for f in s.files]
    total = sum(f.stat().st_size for f in files)
    for s in shots:
        print(f"{s.capture_time:%H:%M:%S}  {s.folder.name}/" + ", ".join(f.name for f in s.files))
    print(f"{len(shots)} prises, {len(files)} fichiers, {total / 1024**2:.0f} Mo en {elapsed:.1f} s"
          + (f" ; retard max sur la cadence : {lag:.2f} s (disque trop lent ?)" if lag > 0.05 else ""),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
