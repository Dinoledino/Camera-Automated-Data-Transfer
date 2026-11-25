"""Banc de simulation du pipeline complet à débit USB bridé (sans appareil, sans gphoto2).

    python bench/pipeline_sim.py [--rates 6 15 25 35] [--scale 10] [--label pc] [--dir DOSSIER]

Le simulateur écrit des photos sur une carte (dossier), le vrai Watcher les détecte, le vrai
Downloader les copie et les vérifie, à travers une source bridée au débit voulu.

Temps accéléré : avec --scale k, les fichiers sont k fois plus petits, les cadences et
intervalles k fois plus rapides ; les durées mesurées sont remultipliées par k. Les rapports
taille/débit et cadence/débit sont conservés, donc les ordres de grandeur aussi. Les coûts
fixes (système de fichiers, base de données) sont en revanche gonflés d'un facteur k : les
résultats sont légèrement pessimistes, d'autant plus que k est grand.

Scénarios (D850 : NEF ~50 Mo, JPEG fine ~20 Mo, 7 i/s) :
- unite   : 10 photos RAW, une toutes les 4 s → délai déclenchement → sauvegarde vérifiée ;
- rafale3 : rafale de 3 s en RAW + JPEG → vidange après la rafale, JPEG d'abord ;
- rafale10: rafale de 10 s en RAW (70 NEF, ~3,5 Go) → vidange après la rafale.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cadt.camera import DirectoryLister  # noqa: E402
from cadt.db import Database  # noqa: E402
from cadt.downloader import DirectorySource, Downloader  # noqa: E402
from cadt.simulator import SimulatedCamera  # noqa: E402
from cadt.throttle import MB, ThrottledSource  # noqa: E402
from cadt.watcher import Watcher  # noqa: E402

RAW_MB, JPEG_MB, FPS = 50, 20, 7.0
POLL_INTERVAL = 2.0          # s, réel
PTP_LATENCY = 0.1            # s par fichier, réel (estimation, À VÉRIFIER sur le D850)

SCENARIOS = {
    "unite": {"shots": 10, "every": 4.0, "jpeg": False},
    "rafale3": {"burst": 3.0, "jpeg": True},
    "rafale10": {"burst": 10.0, "jpeg": False},
}


def run_scenario(name: str, spec: dict, rate: float, k: float, work: Path) -> dict:
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    card, sd = work / "card", work / "sd"
    sim = SimulatedCamera(card, raw_size=int(RAW_MB * MB / k),
                          jpeg_size=int(JPEG_MB * MB / k) if spec["jpeg"] else None, slots=2)
    shot_at: dict[str, float] = {}
    saved_at: dict[str, float] = {}
    done = threading.Event()

    def shooter() -> None:
        t0 = time.monotonic()
        if "burst" in spec:
            n, period = round(spec["burst"] * FPS), 1 / (FPS * k)
        else:
            n, period = spec["shots"], spec["every"] / k
        for i in range(n):
            target = t0 + i * period
            if (d := target - time.monotonic()) > 0:
                time.sleep(d)
            s = sim.shoot()
            now = time.monotonic()
            for f in s.files:
                shot_at[f.name] = now
        done.set()

    with Database(work / "b.db") as db:
        source = ThrottledSource(DirectorySource(card), rate, per_file_latency=PTP_LATENCY / k)
        dl = Downloader(db, source, sd)
        plain = dl.download

        def timed(photo):
            out = plain(photo)
            if out.ok:
                saved_at[photo.name] = time.monotonic()
            return out

        dl.download = timed
        w = Watcher(db, DirectoryLister(card), dl)
        t_start = time.monotonic()
        threading.Thread(target=shooter, daemon=True).start()
        deadline = t_start + 3600 / k
        while time.monotonic() < deadline:
            poll_t = time.monotonic()
            w.poll_once()
            if done.is_set() and shot_at and set(shot_at) <= set(saved_at):
                break
            if (d := poll_t + POLL_INTERVAL / k - time.monotonic()) > 0:
                time.sleep(d)
        unsaved = db.count_unsaved()
        total = db.conn.execute("SELECT count(*) FROM photos").fetchone()[0]

    last_shot = max(shot_at.values())
    lat = [(saved_at[n] - shot_at[n]) * k for n in shot_at if n in saved_at]
    jpegs = [saved_at[n] for n in shot_at if n.upper().endswith(".JPG") and n in saved_at]
    r = {
        "scenario": name, "rate_mb_s": rate, "files": len(shot_at), "saved": len(saved_at),
        "db_photos": total, "unsaved": unsaved,
        "shooting_s": round((last_shot - t_start) * k, 1),
        "drain_after_last_shot_s": round((max(saved_at.values()) - last_shot) * k, 1) if saved_at else None,
        "latency_median_s": round(statistics.median(lat), 1) if lat else None,
        "latency_max_s": round(max(lat), 1) if lat else None,
        "all_jpeg_saved_after_last_shot_s": round((max(jpegs) - last_shot) * k, 1) if jpegs else None,
    }
    shutil.rmtree(work, ignore_errors=True)
    return r


def to_md(res: dict) -> str:
    lines = [f"# Pipeline simulé à débit bridé — {res['label']} ({res['date']})", "",
             f"Échelle de temps : ×{res['scale']} (durées remultipliées). Latence PTP simulée : "
             f"{PTP_LATENCY} s par fichier. Passage du watcher toutes les {POLL_INTERVAL} s, "
             "stabilité sur 2 passages.", ""]
    titles = {"unite": "10 photos RAW, une toutes les 4 s",
              "rafale3": "Rafale de 3 s, RAW + JPEG (21 prises, 42 fichiers, ~1,5 Go)",
              "rafale10": "Rafale de 10 s, RAW (70 NEF, ~3,5 Go)"}
    by = {}
    for r in res["runs"]:
        by.setdefault(r["scenario"], []).append(r)
    for sc, runs in by.items():
        lines += [f"## {titles.get(sc, sc)}", ""]
        if sc == "unite":
            lines += ["| Débit | Délai déclenchement → vérifiée (médian / max) | Sauvegardées |", "|---|---|---|"]
            lines += [f"| {r['rate_mb_s']:g} Mo/s | {r['latency_median_s']} s / {r['latency_max_s']} s | "
                      f"{r['saved']}/{r['files']} |" for r in runs]
        else:
            lines += ["| Débit | Vidange après la dernière prise | Tous les JPEG après | Délai max d'une photo | "
                      "Sauvegardés |", "|---|---|---|---|---|"]
            lines += [f"| {r['rate_mb_s']:g} Mo/s | {r['drain_after_last_shot_s']} s | "
                      f"{r['all_jpeg_saved_after_last_shot_s'] if r['all_jpeg_saved_after_last_shot_s'] is not None else '—'}"
                      f"{' s' if r['all_jpeg_saved_after_last_shot_s'] is not None else ''} | "
                      f"{r['latency_max_s']} s | {r['saved']}/{r['files']} |" for r in runs]
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rates", type=float, nargs="+", default=[6, 15, 25, 35])
    ap.add_argument("--scale", type=float, default=10)
    ap.add_argument("--scenarios", nargs="+", default=list(SCENARIOS), choices=list(SCENARIOS))
    ap.add_argument("--label", default="pc")
    ap.add_argument("--dir", type=Path, default=None, help="dossier de travail (défaut : temporaire)")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "resultats")
    a = ap.parse_args()
    base = a.dir or Path(tempfile.mkdtemp(prefix="cadt-bench-"))
    res = {"label": a.label, "date": datetime.now().isoformat(timespec="seconds"), "scale": a.scale, "runs": []}
    for sc in a.scenarios:
        for rate in a.rates:
            print(f"{sc} à {rate:g} Mo/s…", file=sys.stderr, flush=True)
            r = run_scenario(sc, SCENARIOS[sc], rate, a.scale, base / "work")
            print(f"  {r}", file=sys.stderr, flush=True)
            res["runs"].append(r)
    a.out.mkdir(parents=True, exist_ok=True)
    stem = f"pipeline-{a.label}-{res['date'][:10]}"
    (a.out / f"{stem}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    (a.out / f"{stem}.md").write_text(to_md(res))
    print(to_md(res))
    if a.dir is None:
        shutil.rmtree(base, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
