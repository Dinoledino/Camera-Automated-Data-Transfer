"""Sonde du jour J : teste l'appareil photo réel et recommande une configuration (voir PLAN.md, §b).

À lancer sur le téléphone, D850 branché. Elle guide pas à pas, mesure, et écrit :
- probe_report.json / probe_report.md : résultats lisibles par un humain ;
- config.recommended.toml : la combinaison qui a marché ;
- logs/<étape>.log : journaux complets de libgphoto2, pour analyser plus tard sur le PC.

Garanties :
- aucune dépendance réseau ;
- la sonde ne MODIFIE AUCUN réglage de l'appareil (lecture seule de la configuration) ;
- elle ne supprime rien sur l'appareil (le test de suppression viendra dans une
  version ultérieure, limité aux photos créées pendant la sonde) ;
- reprenable : chaque étape réussie est enregistrée ; une relance reprend à la
  première étape non réussie (--from ÉTAPE pour forcer, --redo pour tout refaire).

Avec --card DOSSIER, l'« appareil » est un dossier lu par le pilote Directory Browse,
et les prises de vue demandées sont faites par le simulateur : la sonde tourne alors
entièrement sans D850 (c'est ainsi qu'elle est testée).

    python -m cadt.probe --out sonde/                 # avec le vrai appareil
    python -m cadt.probe --out sonde/ --card CARTE    # avec le simulateur
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import gphoto2 as gp

from .db import Database
from .downloader import Downloader, SourceError
from .gphoto_source import CameraFile, Gphoto2Source
from .simulator import SimulatedCamera
from .watcher import Watcher

STATE_FILE = "probe_state.json"
LOG_CAP_BYTES = 50 * 1024 * 1024  # par étape : les journaux DEBUG d'une rafale peuvent être énormes
# Noms d'appareils détectés qui ne sont pas des appareils photo (ex. le téléphone lui-même).
NOT_A_CAMERA = ("MTP", "Galaxy", "Android", "Phone", "Directory Browse")


# ------------------------------------------------------------------ dialogue

class Ui:
    """Questions à l'utilisateur. En mode automatique, les réponses sont prises par défaut."""

    def __init__(self, auto: bool = False, out: Callable[[str], None] = print):
        self.auto = auto
        self.out = out

    def say(self, text: str = "") -> None:
        self.out(text)

    def ask(self, question: str, default: str = "") -> str:
        if self.auto:
            self.out(f"{question} [{default}] (auto)")
            return default
        answer = input(f"{question} [{default}] ").strip()
        return answer or default

    def confirm(self, question: str, default: bool = True) -> bool:
        d = "o" if default else "n"
        return self.ask(f"{question} (o/n)", d).lower().startswith("o")

    def wait_enter(self, text: str, done: threading.Event) -> None:
        """Affiche `text` et lève `done` quand l'utilisateur appuie sur Entrée."""
        self.out(text)
        if self.auto:
            return  # c'est le simulateur qui lèvera `done`
        threading.Thread(target=lambda: (input(), done.set()), daemon=True).start()


# ------------------------------------------------------------------ contexte

@dataclass
class Context:
    out_dir: Path
    ui: Ui
    source: Gphoto2Source
    simulator: SimulatedCamera | None
    shots: int = 5
    burst_seconds: float = 3.0
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def camera(self) -> gp.Camera:
        return self.source._cam()

    def result(self, step: str) -> dict[str, Any]:
        return self.state.get("steps", {}).get(step, {}).get("result", {})

    def shoot(self, n: int, done: threading.Event, burst: bool = False) -> None:
        """Demande des prises de vue (ou les fait faire au simulateur)."""
        if self.simulator is not None:
            def run() -> None:
                time.sleep(0.3)
                if burst:
                    self.simulator.burst(self.burst_seconds)
                else:
                    for _ in range(n):
                        self.simulator.shoot()
                        time.sleep(0.2)
                time.sleep(0.3)
                done.set()
            threading.Thread(target=run, daemon=True).start()
            what = f"rafale de {self.burst_seconds:g} s" if burst else f"{n} photos"
            self.ui.say(f"  (simulateur : {what})")
        else:
            if burst:
                text = (f"  → Fais maintenant une RAFALE d'environ {self.burst_seconds:g} s "
                        f"(mode CH), puis appuie sur Entrée.")
            else:
                text = f"  → Prends maintenant {n} photos, une par une, puis appuie sur Entrée."
            self.ui.wait_enter(text, done)


@dataclass
class Step:
    id: str
    title: str
    run: Callable[[Context], dict[str, Any]]


class StepFailed(Exception):
    pass


# ------------------------------------------------------------------ journaux libgphoto2

class GphotoLog:
    """Capture les journaux de libgphoto2 (niveau DEBUG) dans un fichier, le temps d'une étape."""

    def __init__(self, path: Path):
        self.path = path
        self._file = None
        self._handle = None

    def __enter__(self) -> "GphotoLog":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "w", encoding="utf-8", errors="replace")
        f = self._file

        written = [0]

        def callback(level: int, domain: str, message: str, data: object = None) -> None:
            if f.closed or written[0] > LOG_CAP_BYTES:
                return
            line = f"{time.monotonic():.3f} {domain}: {message}\n"
            written[0] += len(line)
            f.write(line if written[0] <= LOG_CAP_BYTES else "… journal tronqué (plafond atteint)\n")

        # Renvoie [code, poignée] ; libérer la poignée retire le callback, d'où la référence gardée.
        self._handle = gp.check_result(gp.gp_log_add_func(gp.GP_LOG_DEBUG, callback))
        return self

    def __exit__(self, *exc: object) -> None:
        self._handle = None
        self._file.close()


# ------------------------------------------------------------------ étapes

def step_system(ctx: Context) -> dict[str, Any]:
    dest = ctx.out_dir
    usage = shutil.disk_usage(dest)
    android = shutil.which("getprop") is not None
    r = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "os": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "libgphoto2": list(gp.gp_library_version(gp.GP_VERSION_SHORT)),
        "python_gphoto2": gp.__version__,
        "root": hasattr(os, "geteuid") and os.geteuid() == 0,
        "android": android,
        "free_bytes_out_dir": usage.free,
    }
    ctx.ui.say(f"  libgphoto2 {r['libgphoto2'][0]}, Python {r['python']}, root : {r['root']}, "
               f"espace libre : {usage.free / 1024**3:.1f} Go")
    return r


def autodetect() -> list[tuple[str, str]]:
    """Appareils USB reconnus par libgphoto2 : [(modèle, port)]."""
    found = gp.Camera.autodetect()
    return [(found.get_name(i), found.get_value(i)) for i in range(len(found))]


def step_detect(ctx: Context) -> dict[str, Any]:
    detected = autodetect()
    ctx.ui.say(f"  détectés : {detected or 'aucun'}")
    if ctx.simulator is None:
        cameras = [(n, p) for n, p in detected if not any(x.lower() in n.lower() for x in NOT_A_CAMERA)]
        if not cameras:
            raise StepFailed("aucun appareil photo détecté (vérifie le câble, allume l'appareil)")
        if len(cameras) > 1:
            choices = ", ".join(f"{i}: {n} ({p})" for i, (n, p) in enumerate(cameras))
            idx = int(ctx.ui.ask(f"  Plusieurs appareils : {choices}. Lequel ?", "0"))
            cameras = [cameras[idx]]
        ctx.source.model, ctx.source.port = cameras[0]
    t = time.monotonic()
    ctx.source.refresh()
    connect_s = time.monotonic() - t
    a = ctx.camera.get_abilities()
    r = {
        "detected": detected,
        "model": a.model,
        "port": ctx.source.port,
        "connect_seconds": round(connect_s, 3),
        "operations": a.operations,
        "file_operations": a.file_operations,
        "can_delete": bool(a.file_operations & gp.GP_FILE_OPERATION_DELETE),
    }
    for name in ("get_summary",):
        try:
            r["summary"] = str(getattr(ctx.camera, name)())
        except gp.GPhoto2Error as e:
            r["summary"] = f"non disponible ({e})"
    ctx.ui.say(f"  appareil : {a.model} sur {ctx.source.port}, connexion en {connect_s:.2f} s")
    return r


def step_config(ctx: Context) -> dict[str, Any]:
    """Lecture seule de la configuration : firmware, batterie, support d'enregistrement…"""
    try:
        root = ctx.camera.get_config()
    except gp.GPhoto2Error as e:
        ctx.ui.say(f"  configuration non disponible ({e})")
        return {"available": False, "error": str(e)}
    values: dict[str, Any] = {}

    def walk(widget: Any, path: str) -> None:
        for i in range(widget.count_children()):
            child = widget.get_child(i)
            name = f"{path}/{child.get_name()}"
            if child.get_type() in (gp.GP_WIDGET_SECTION, gp.GP_WIDGET_WINDOW):
                walk(child, name)
            else:
                try:
                    values[name] = child.get_value()
                except gp.GPhoto2Error:
                    values[name] = None

    walk(root, "")
    (ctx.out_dir / "camera_config.json").write_text(json.dumps(values, indent=1, ensure_ascii=False))
    interesting = {k: v for k, v in values.items()
                   if any(s in k.lower() for s in ("deviceversion", "serialnumber", "batterylevel",
                                                   "capturetarget", "recordingmedia", "manufacturer",
                                                   "cameramodel", "datetime", "imagequality", "usb"))}
    for k, v in interesting.items():
        ctx.ui.say(f"  {k} = {v}")
    return {"available": True, "count": len(values), "interesting": interesting}


def step_storage(ctx: Context) -> dict[str, Any]:
    storages = []
    try:
        for s in ctx.camera.get_storageinfo():
            storages.append({"basedir": s.basedir, "label": s.label, "description": s.description,
                             "capacity_kb": s.capacitykbytes, "free_kb": s.freekbytes})
    except gp.GPhoto2Error as e:
        storages.append({"error": str(e)})
    t = time.monotonic()
    ctx.source.refresh()
    refresh_s = time.monotonic() - t
    t = time.monotonic()
    files = ctx.source.list_files()
    list_s = time.monotonic() - t
    t = time.monotonic()
    ctx.source.list_files()
    relist_s = time.monotonic() - t
    by_ext: dict[str, int] = {}
    for f in files:
        ext = Path(f.name).suffix.upper()
        by_ext[ext] = by_ext.get(ext, 0) + 1
    ctx.ui.say(f"  {len(storages)} carte(s) ; {len(files)} fichiers {by_ext} ; "
               f"liste en {list_s:.2f} s (puis {relist_s:.2f} s en cache), reconnexion {refresh_s:.2f} s")
    return {"storages": storages, "files": len(files), "by_ext": by_ext,
            "list_seconds": round(list_s, 3), "relist_cached_seconds": round(relist_s, 3),
            "refresh_seconds": round(refresh_s, 3)}


def _pick(files: list[CameraFile], ext: str) -> CameraFile | None:
    cands = [f for f in files if f.name.upper().endswith(ext)]
    return max(cands, key=lambda f: (f.capture_time, f.name)) if cands else None


def step_read(ctx: Context) -> dict[str, Any]:
    """Débit de lecture et cohérence : le même fichier lu par chaque méthode doit avoir le même hash."""
    files = ctx.source.list_files()
    results: dict[str, Any] = {}
    for ext in (".NEF", ".JPG"):
        f = _pick(files, ext)
        if f is None:
            results[ext] = {"skipped": "aucun fichier de ce type sur la carte"}
            continue
        per_method: dict[str, Any] = {}
        for method in ("partial", "full"):
            ctx.source._partial_reads = method == "partial"
            h, n = hashlib.sha256(), 0
            t = time.monotonic()
            try:
                for chunk in ctx.source.iter_chunks(f.folder, f.name):
                    h.update(chunk)
                    n += len(chunk)
            except SourceError as e:
                per_method[method] = {"ok": False, "error": str(e)}
                continue
            dt = time.monotonic() - t
            fell_back = method == "partial" and not ctx.source._partial_reads
            per_method[method] = {"ok": n == f.size, "bytes": n, "seconds": round(dt, 3),
                                  "mb_per_s": round(n / 1024**2 / dt, 1) if dt else None,
                                  "sha256": h.hexdigest(), "fell_back_to_full": fell_back}
        hashes = {m["sha256"] for m in per_method.values() if m.get("ok")}
        results[ext] = {"file": f"{f.folder}/{f.name}", "size": f.size, "methods": per_method,
                        "consistent": len(hashes) == 1}
        for m, v in per_method.items():
            if v.get("ok"):
                extra = " (repli sur lecture entière)" if v["fell_back_to_full"] else ""
                ctx.ui.say(f"  {f.name} [{m}] : {v['mb_per_s']} Mo/s{extra}")
            else:
                ctx.ui.say(f"  {f.name} [{m}] : ÉCHEC {v.get('error', 'taille incorrecte')}")
    ctx.source._partial_reads = True
    partial_ok = any(r.get("methods", {}).get("partial", {}).get("ok")
                     and not r["methods"]["partial"]["fell_back_to_full"]
                     for r in results.values())
    return {"files": results, "partial_reads_supported": partial_ok}


def step_detection(ctx: Context) -> dict[str, Any]:
    """Peut-on déclencher en étant branché ? Les nouvelles photos sont-elles visibles, et comment ?"""
    before = {f.key for f in ctx.source.list_files()}
    done = threading.Event()
    events: list[dict[str, Any]] = []
    t0 = time.monotonic()
    ctx.shoot(ctx.shots, done)
    while not done.is_set() and time.monotonic() - t0 < 600:
        try:
            kind, data = ctx.camera.wait_for_event(500)
        except gp.GPhoto2Error as e:
            events.append({"t": round(time.monotonic() - t0, 3), "error": str(e)})
            time.sleep(0.5)
            continue
        if kind == gp.GP_EVENT_TIMEOUT:
            continue
        e = {"t": round(time.monotonic() - t0, 3), "type": int(kind)}
        if kind in (gp.GP_EVENT_FILE_ADDED, gp.GP_EVENT_FOLDER_ADDED, gp.GP_EVENT_FILE_CHANGED):
            e["path"] = f"{data.folder}/{data.name}"
        else:
            e["data"] = str(data)[:200]
        events.append(e)
    shoot_window = time.monotonic() - t0

    new_cached = [f for f in ctx.source.list_files() if f.key not in before]
    ctx.source.refresh()
    new_after_refresh = [f for f in ctx.source.list_files() if f.key not in before]
    added = [e for e in events if e.get("type") == gp.GP_EVENT_FILE_ADDED]
    r = {
        "shoot_window_seconds": round(shoot_window, 1),
        "events": events,
        "file_added_events": len(added),
        "new_files_without_refresh": len(new_cached),
        "new_files_after_refresh": len(new_after_refresh),
        "new_files": [f"{f.folder}/{f.name}" for f in new_after_refresh],
        "shooting_while_connected": len(new_after_refresh) > 0,
    }
    ctx.ui.say(f"  nouvelles photos : {len(new_after_refresh)} (sans reconnexion : {len(new_cached)}) ; "
               f"événements « fichier ajouté » : {len(added)}")
    if not new_after_refresh:
        raise StepFailed("aucune nouvelle photo vue : déclenchement impossible en étant branché, "
                         "ou photos non écrites sur la carte")
    return r


def step_burst(ctx: Context) -> dict[str, Any]:
    """Rafale réelle, puis temps de vidange avec le vrai moteur (watcher + downloader)."""
    work = ctx.out_dir / "burst"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    with Database(work / "probe.db") as db:
        refresh = not (ctx.result("detection").get("new_files_without_refresh", 0) > 0)
        downloader = Downloader(db, ctx.source, work / "sd")
        download_time = [0.0]
        plain_download = downloader.download

        def timed_download(photo):  # mesure le temps passé à télécharger, hors attente de la rafale
            t = time.monotonic()
            try:
                return plain_download(photo)
            finally:
                download_time[0] += time.monotonic() - t

        downloader.download = timed_download
        w = Watcher(db, ctx.source, downloader, refresh_each_poll=refresh)
        w.ignore_existing()
        done = threading.Event()
        t0 = time.monotonic()
        ctx.shoot(0, done, burst=True)
        while not done.is_set():
            w.poll_once()
        shot_end = time.monotonic()
        idle = 0
        while idle < 3 and time.monotonic() - shot_end < 900:
            r = w.poll_once()
            idle = idle + 1 if (r.new == 0 and not r.outcomes and r.pending_stability == 0) else 0
        t_end = time.monotonic()
        rows = db.conn.execute("SELECT state, size FROM photos").fetchall()
    saved = [r for r in rows if r["state"] == "verified"]
    total = sum(r["size"] for r in saved)
    r = {
        "photos": len(rows),
        "verified": len(saved),
        "bytes": total,
        "seconds_total": round(t_end - t0, 1),
        "drain_after_burst_seconds": round(t_end - shot_end, 1),
        "download_seconds": round(download_time[0], 2),
        "mb_per_s_download": round(total / 1024**2 / download_time[0], 1) if download_time[0] else None,
        "refresh_each_poll": refresh,
    }
    shutil.rmtree(work / "sd", ignore_errors=True)  # la sonde ne garde pas les copies
    ctx.ui.say(f"  {len(saved)}/{len(rows)} fichiers vérifiés, {total / 1024**2:.0f} Mo ; "
               f"vidange après la rafale : {r['drain_after_burst_seconds']} s ; "
               f"débit de téléchargement (copie + vérification) : {r['mb_per_s_download']} Mo/s")
    if len(saved) != len(rows) or not rows:
        raise StepFailed("toutes les photos de la rafale n'ont pas été sauvegardées")
    return r


STEPS = [
    Step("system", "Informations système", step_system),
    Step("detect", "Détection de l'appareil", step_detect),
    Step("config", "Configuration de l'appareil (lecture seule)", step_config),
    Step("storage", "Cartes et liste des fichiers", step_storage),
    Step("read", "Lecture d'un NEF et d'un JPEG : débit et cohérence", step_read),
    Step("detection", "Déclenchement pendant le branchement, détection des nouvelles photos", step_detection),
    Step("burst", "Rafale réelle et vidange", step_burst),
]


# ------------------------------------------------------------------ déroulé, reprise, rapport

def _save_state(out_dir: Path, state: dict[str, Any]) -> None:
    tmp = out_dir / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False, default=str))
    os.replace(tmp, out_dir / STATE_FILE)


def _load_state(out_dir: Path) -> dict[str, Any]:
    path = out_dir / STATE_FILE
    return json.loads(path.read_text()) if path.exists() else {"steps": {}}


def run_probe(ctx: Context, start_from: str | None = None, redo: bool = False) -> dict[str, Any]:
    ctx.state = {"steps": {}} if redo else _load_state(ctx.out_dir)
    steps_state = ctx.state.setdefault("steps", {})
    ids = [s.id for s in STEPS]
    if start_from is not None and start_from not in ids:
        raise ValueError(f"étape inconnue : {start_from} (étapes : {', '.join(ids)})")
    forced = ids[ids.index(start_from):] if start_from else []
    for i, step in enumerate(STEPS, 1):
        prev = steps_state.get(step.id)
        if prev and prev.get("status") == "ok" and step.id not in forced:
            ctx.ui.say(f"[{i}/{len(STEPS)}] {step.title} : déjà fait")
            # Étape déjà faite, mais la connexion doit être rétablie pour la suite.
            if step.id == "detect":
                ctx.source.model = prev["result"].get("model", ctx.source.model)
                ctx.source.port = prev["result"].get("port", ctx.source.port)
            continue
        ctx.ui.say(f"[{i}/{len(STEPS)}] {step.title}")
        entry: dict[str, Any] = {"started": datetime.now().isoformat(timespec="seconds")}
        try:
            with GphotoLog(ctx.out_dir / "logs" / f"{i:02d}-{step.id}.log"):
                entry["result"] = step.run(ctx)
            entry["status"] = "ok"
        except (StepFailed, SourceError, gp.GPhoto2Error) as e:
            entry["status"] = "failed"
            entry["error"] = f"{type(e).__name__}: {e}"
            ctx.ui.say(f"  ÉCHEC : {e}")
        entry["ended"] = datetime.now().isoformat(timespec="seconds")
        steps_state[step.id] = entry
        _save_state(ctx.out_dir, ctx.state)
        if entry["status"] == "failed" and not ctx.ui.confirm("  Continuer avec les étapes suivantes ?"):
            break
    write_reports(ctx.out_dir, ctx.state)
    return ctx.state


def recommend(state: dict[str, Any]) -> dict[str, Any]:
    """Déduit la configuration à utiliser de ce qui a marché."""
    res = lambda s: state["steps"].get(s, {}).get("result", {})  # noqa: E731
    det, read = res("detection"), res("read")
    events_ok = det.get("file_added_events", 0) >= det.get("new_files_after_refresh", 1) > 0
    return {
        "camera": {"model": res("detect").get("model"), "port": res("detect").get("port")},
        "detection": {
            "mode": "hybrid" if events_ok else "polling",
            "refresh_each_poll": not det.get("new_files_without_refresh", 0) > 0,
            "stable_polls": 2,
        },
        "download": {"partial_reads": bool(read.get("partial_reads_supported"))},
    }


def _toml(d: dict[str, Any]) -> str:
    lines = ["# Généré par cadt.probe : la combinaison qui a marché avec cet appareil.", ""]
    for section, values in d.items():
        lines.append(f"[{section}]")
        for k, v in values.items():
            if isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                lines.append(f"{k} = {v}")
            elif v is not None:
                lines.append(f'{k} = "{v}"')
        lines.append("")
    return "\n".join(lines)


def write_reports(out_dir: Path, state: dict[str, Any]) -> None:
    rec = recommend(state)
    report = {"steps": state["steps"], "recommended": rec}
    (out_dir / "probe_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    (out_dir / "config.recommended.toml").write_text(_toml(rec))
    md = ["# Rapport de sonde", "", "| Étape | Résultat | Détail |", "|---|---|---|"]
    for step in STEPS:
        e = state["steps"].get(step.id)
        if e is None:
            md.append(f"| {step.title} | – | non lancée |")
            continue
        mark = "✅" if e["status"] == "ok" else "❌"
        md.append(f"| {step.title} | {mark} | {_summary(step.id, e)} |")
    md += ["", "## Configuration recommandée", "", "```toml", _toml(rec), "```", "",
           "Journaux libgphoto2 complets : `logs/`. Détails : `probe_report.json`."]
    (out_dir / "probe_report.md").write_text("\n".join(md))


def _summary(step_id: str, e: dict[str, Any]) -> str:
    if e["status"] != "ok":
        return e.get("error", "").replace("|", "/")
    r = e.get("result", {})
    if step_id == "system":
        return f"libgphoto2 {r['libgphoto2'][0]}, root : {r['root']}"
    if step_id == "detect":
        return f"{r['model']} ({r['port']})"
    if step_id == "config":
        return f"{r.get('count', 0)} réglages lus" if r.get("available") else "non disponible"
    if step_id == "storage":
        return f"{len(r['storages'])} carte(s), {r['files']} fichiers, liste en {r['list_seconds']} s"
    if step_id == "read":
        parts = []
        for ext, v in r["files"].items():
            for m, x in v.get("methods", {}).items():
                if x.get("ok"):
                    parts.append(f"{ext} {m} {x['mb_per_s']} Mo/s")
        return ", ".join(parts) or "aucun fichier lu"
    if step_id == "detection":
        return (f"{r['new_files_after_refresh']} nouvelles photos, "
                f"{r['file_added_events']} événements, sans reconnexion : {r['new_files_without_refresh']}")
    if step_id == "burst":
        return (f"{r['verified']}/{r['photos']} vérifiées, {r['mb_per_s_download']} Mo/s, "
                f"vidange après la rafale {r['drain_after_burst_seconds']} s")
    return ""


# ------------------------------------------------------------------ ligne de commande

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sonde : teste l'appareil photo et recommande une configuration.")
    p.add_argument("--out", type=Path, required=True, help="dossier des résultats (reprise possible)")
    p.add_argument("--card", type=Path, help="simulateur : ce dossier joue le rôle de l'appareil")
    p.add_argument("--from", dest="start_from", help="reprendre à cette étape (et refaire les suivantes)")
    p.add_argument("--redo", action="store_true", help="tout refaire")
    p.add_argument("--auto", action="store_true", help="répondre aux questions par défaut (tests)")
    p.add_argument("--shots", type=int, default=5, help="photos à prendre à l'étape de détection")
    p.add_argument("--burst", type=float, default=3.0, help="durée de la rafale, en secondes")
    p.add_argument("--sim-raw-size", type=int, default=5 * 1024 * 1024, help="taille d'un NEF simulé")
    args = p.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    simulator = None
    if args.card:
        simulator = SimulatedCamera(args.card, raw_size=args.sim_raw_size, jpeg_size=args.sim_raw_size // 4)
        if not any(args.card.glob("DCIM/*/DSC_*")):
            simulator.shoot()  # la carte doit contenir au moins une photo pour l'étape de lecture
        source = Gphoto2Source.directory(str(args.card))
    else:
        source = Gphoto2Source()
    ctx = Context(args.out, Ui(auto=args.auto), source, simulator, shots=args.shots, burst_seconds=args.burst)
    try:
        state = run_probe(ctx, start_from=args.start_from, redo=args.redo)
    finally:
        source.close()
    failed = [k for k, v in state["steps"].items() if v.get("status") != "ok"]
    print(f"\nRapport : {args.out / 'probe_report.md'}"
          + (f" — étapes en échec : {', '.join(failed)}" if failed else " — toutes les étapes ont réussi"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
