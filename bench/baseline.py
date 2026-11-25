"""Mesures de base sur la machine qui fera la sauvegarde (phase 1).

    python bench/baseline.py --dir DOSSIER --nef UN_NEF_DE_D850 [--size-mb 1024] [--out bench/resultats]

Mesure, dans DOSSIER (à placer sur le support visé : microSD, mémoire interne…) :
1. le stockage : écriture séquentielle avec fsync, puis relecture cache vidé ;
2. le processeur : SHA-256 en mémoire et d'un fichier de 50 Mo ;
3. le moteur réel : copie vérifiée de NEF par le Downloader (copie, hash, fsync, relecture,
   renommage), sans USB : c'est le plafond côté téléphone ;
4. l'indexation réelle d'un NEF : exiftool, miniature, aperçu ; et la mémoire maximale.

Écrit <out>/baseline-<machine>-<date>.json et .md. Sans dépendance réseau ni gphoto2.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cadt.db import Database  # noqa: E402
from cadt.downloader import DirectorySource, Downloader  # noqa: E402

MB = 1024 * 1024


def drop_cache(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)


def bench_storage(d: Path, size_mb: int) -> dict:
    f = d / "bench-storage.bin"
    block = os.urandom(MB)
    t = time.monotonic()
    with open(f, "wb") as out:
        for _ in range(size_mb):
            out.write(block)
        out.flush()
        os.fsync(out.fileno())
    write_s = time.monotonic() - t
    drop_cache(f)
    t = time.monotonic()
    with open(f, "rb") as inp:
        while inp.read(MB):
            pass
    read_s = time.monotonic() - t
    t = time.monotonic()
    with open(f, "rb") as inp:
        while inp.read(MB):
            pass
    cached_s = time.monotonic() - t
    f.unlink()
    return {"size_mb": size_mb, "write_mb_s": round(size_mb / write_s, 1),
            "read_mb_s": round(size_mb / read_s, 1), "read_cached_mb_s": round(size_mb / cached_s, 1),
            "read_is_probably_cached": cached_s > 0 and read_s / cached_s < 1.5}


def bench_cpu(d: Path) -> dict:
    data = os.urandom(50 * MB)
    t = time.monotonic()
    hashlib.sha256(data).hexdigest()
    mem_s = time.monotonic() - t
    f = d / "bench-50mb.bin"
    f.write_bytes(data)
    drop_cache(f)
    t = time.monotonic()
    h = hashlib.sha256()
    with open(f, "rb") as inp:
        while chunk := inp.read(MB):
            h.update(chunk)
    file_s = time.monotonic() - t
    f.unlink()
    return {"sha256_50mb_memory_s": round(mem_s, 3), "sha256_mb_s": round(50 / mem_s, 1),
            "sha256_50mb_file_s": round(file_s, 3)}


def bench_pipeline(d: Path, nef: Path, copies: int) -> dict:
    """Copie vérifiée de `copies` NEF, d'un dossier « appareil » vers un dossier « sauvegarde »."""
    work = d / "bench-pipeline"
    shutil.rmtree(work, ignore_errors=True)
    cam = work / "camera" / "DCIM" / "100NC850"
    cam.mkdir(parents=True)
    for i in range(copies):
        shutil.copyfile(nef, cam / f"DSC_{i + 1:04d}.NEF")
    for f in cam.iterdir():
        drop_cache(f)
    size = nef.stat().st_size
    with Database(work / "b.db") as db:
        for i in range(copies):
            db.add_detected("/DCIM/100NC850", f"DSC_{i + 1:04d}.NEF", size, f"2026-01-01T00:00:{i:02d}")
        dl = Downloader(db, DirectorySource(work / "camera"), work / "sd")
        t = time.monotonic()
        outcomes = dl.drain()
        dt = time.monotonic() - t
    ok = sum(o.ok for o in outcomes)
    shutil.rmtree(work, ignore_errors=True)
    return {"files": copies, "verified": ok, "file_mb": round(size / MB, 1), "seconds": round(dt, 2),
            "seconds_per_file": round(dt / copies, 2), "mb_s": round(copies * size / MB / dt, 1)}


def bench_index(d: Path, nef: Path) -> dict:
    from cadt.exif import ExifTool
    from cadt.indexer import Indexer
    work = d / "bench-index"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    results = {}
    with Database(work / "b.db") as db, ExifTool() as et:
        ix = Indexer(db, work, et)
        photos = []
        for i in range(3):
            f = work / f"DSC_{i + 1:04d}.NEF"
            shutil.copyfile(nef, f)
            p, _ = db.add_detected("/d", f.name, f.stat().st_size, f"2026-01-01T00:00:0{i}")
            db.claim_next()
            db.mark_downloaded(p.id, "a" * 64)
            photos.append(db.mark_verified(p.id, str(f), "a" * 64))
        t = time.monotonic()
        et.read(photos[0].local_path)
        results["exiftool_first_call_s"] = round(time.monotonic() - t, 3)
        t = time.monotonic()
        et.read(photos[0].local_path)
        results["exiftool_next_call_s"] = round(time.monotonic() - t, 3)
        times = []
        for p in photos:
            t = time.monotonic()
            m = ix.index(p)
            times.append(time.monotonic() - t)
            if m.error:
                results["error"] = m.error
        results["index_nef_s"] = [round(x, 2) for x in times]
    shutil.rmtree(work, ignore_errors=True)
    return results


def machine() -> dict:
    info = {"platform": platform.platform(), "machine": platform.machine(), "python": platform.python_version(),
            "cpus": os.cpu_count()}
    try:
        info["cpu_max_mhz"] = sorted({int(Path(p).read_text()) // 1000 for p in
                                      map(str, Path("/sys/devices/system/cpu").glob("cpu*/cpufreq/cpuinfo_max_freq"))})
    except (OSError, ValueError):
        pass
    try:
        info["mem_total_mb"] = int(next(l for l in open("/proc/meminfo") if l.startswith("MemTotal")).split()[1]) // 1024
    except (OSError, StopIteration):
        pass
    return info


def to_md(r: dict) -> str:
    s, c, p, i = r["storage"], r["cpu"], r["pipeline"], r["index"]
    lines = [
        f"# Mesures de base — {r['label']} ({r['date']})", "",
        f"Machine : {r['machine']}", f"Dossier mesuré : `{r['dir']}`", "",
        "| Mesure | Résultat |", "|---|---|",
        f"| Écriture séquentielle ({s['size_mb']} Mo, fsync) | {s['write_mb_s']} Mo/s |",
        f"| Lecture, cache vidé | {s['read_mb_s']} Mo/s"
        + (" (⚠ probablement encore en cache)" if s["read_is_probably_cached"] else "") + " |",
        f"| SHA-256 en mémoire | {c['sha256_mb_s']} Mo/s ({c['sha256_50mb_memory_s']} s pour 50 Mo) |",
        f"| SHA-256 d'un fichier de 50 Mo | {c['sha256_50mb_file_s']} s |",
        f"| Copie vérifiée de {p['files']} NEF de {p['file_mb']} Mo (sans USB) | {p['mb_s']} Mo/s, "
        f"{p['seconds_per_file']} s par NEF, {p['verified']}/{p['files']} vérifiés |",
        f"| exiftool : 1er appel / suivants | {i['exiftool_first_call_s']} s / {i['exiftool_next_call_s']} s |",
        f"| Indexation d'un NEF (EXIF, miniature, aperçu) | {', '.join(f'{x} s' for x in i['index_nef_s'])} |",
        f"| Mémoire maximale du processus | {r['max_rss_mb']} Mo |",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--nef", type=Path, required=True)
    ap.add_argument("--size-mb", type=int, default=1024)
    ap.add_argument("--copies", type=int, default=5)
    ap.add_argument("--label", default=platform.node())
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "resultats")
    a = ap.parse_args()
    a.dir.mkdir(parents=True, exist_ok=True)
    r = {"label": a.label, "date": datetime.now().isoformat(timespec="seconds"), "dir": str(a.dir),
         "machine": machine()}
    for name, fn in (("storage", lambda: bench_storage(a.dir, a.size_mb)), ("cpu", lambda: bench_cpu(a.dir)),
                     ("pipeline", lambda: bench_pipeline(a.dir, a.nef, a.copies)),
                     ("index", lambda: bench_index(a.dir, a.nef))):
        print(f"{name}…", file=sys.stderr, flush=True)
        r[name] = fn()
        print(f"  {r[name]}", file=sys.stderr, flush=True)
    r["max_rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    a.out.mkdir(parents=True, exist_ok=True)
    stem = f"baseline-{a.label}-{r['date'][:10]}"
    (a.out / f"{stem}.json").write_text(json.dumps(r, indent=1, ensure_ascii=False))
    (a.out / f"{stem}.md").write_text(to_md(r))
    print(to_md(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
