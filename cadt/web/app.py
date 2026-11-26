"""Interface de tri, ouverte dans le navigateur du téléphone (phase 5).

Starlette + Jinja2, pages générées côté serveur, un peu de JavaScript pour les boutons et le
balayage. Processus séparé du watcher : la base SQLite (mode WAL) est partagée.

Sécurité : le serveur n'écoute que le téléphone (127.0.0.1 par défaut). Toute action (POST)
exige l'en-tête X-CADT : une page web étrangère ouverte dans le navigateur ne peut pas en envoyer
(le navigateur bloque les en-têtes personnalisés vers une autre origine sans autorisation).

Vocabulaire de l'interface : sans jargon technique (voir PLAN.md, maquette validée).
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from ..db import Database
from ..exif import format_exposure
from ..storage import StoragePool
from ..sessions import move_photos
from ..trash import Trash
from . import queries as q

HERE = Path(__file__).parent
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
          "octobre", "novembre", "décembre"]
DEFAULT_DETAILS = {"reglages": True, "objectif": True, "heure": True, "fichier": True}
# Réglages d'apparence : (valeur par défaut, valeurs permises)
THEMES = {"sombre": "Sombre", "clair": "Clair"}
ACCENTS = {"jaune": "Jaune", "bleu": "Bleu", "vert": "Vert", "orange": "Orange"}
THUMBS = {"petites": ("Petites", 4), "moyennes": ("Moyennes", 3), "grandes": ("Grandes", 2)}
TRASH_DELAYS = {"1": "1 heure", "24": "24 heures", "168": "7 jours"}
DETAILS = [  # (clé, libellé, exemple)
    ("reglages", "Réglages de prise de vue", "35 mm · f/9 · 1/160 s · ISO 64"),
    ("objectif", "Appareil et objectif", "NIKON D850 · 35 mm f/1.4"),
    ("heure", "Date et heure", "23 novembre 2026 · 14:32:05"),
    ("fichier", "Nom du fichier", "DSC_0044.NEF"),
]
PLACEHOLDER_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3 2">'
                   '<rect width="3" height="2" fill="#26282D"/></svg>')


@dataclass
class WebConfig:
    db_path: Path
    pool: StoragePool
    indexer_factory: Callable[[Database], object] | None = None  # pour les aperçus à la demande
    _lock: threading.Lock = field(default_factory=threading.Lock)


def human_size(n: int) -> str:
    for unit, div in (("To", 1024 ** 4), ("Go", 1024 ** 3), ("Mo", 1024 ** 2)):
        if n >= div:
            v = n / div
            return f"{v:.0f} {unit}" if v >= 10 else f"{v:.1f} {unit}".replace(".", ",")
    return f"{n // 1024} Ko"


def french_datetime(iso: str | None) -> str:
    if not iso:
        return ""
    d = datetime.fromisoformat(iso[:19])
    return f"{d.day} {MONTHS[d.month - 1]} {d.year} · {d:%H:%M:%S}"


def file_name(photo) -> str:
    """Nom actuel du fichier sur le téléphone (celui d'origine tant qu'il n'est pas copié)."""
    return Path(photo.local_path).name if photo.local_path else photo.name


def short_name(name: str, width: int = 16) -> str:
    """Raccourcit par le début : « 2026-11-26_Rando-Vercors_0004.NEF » → « …Vercors_0004.NEF »."""
    return name if len(name) <= width else "…" + name[-(width - 1):]


def storage_label(root: Path) -> str:
    """« Mémoire interne » ou « Carte SD », d'après le chemin de l'emplacement."""
    p = root.as_posix().lower()
    if re.search(r"/storage/[0-9a-f]{4}-[0-9a-f]{4}", p) or re.search(r"(^|/)(carte|sd|microsd)[^/]*$", p):
        return "Carte SD"
    return "Mémoire interne"


def local_time(iso: str) -> str:
    """Horodatage UTC de la base → « à 14:52 » aujourd'hui, sinon « le 23 novembre à 14:52 »."""
    d = datetime.fromisoformat(iso).astimezone()
    if d.date() == datetime.now().astimezone().date():
        return f"à {d:%H:%M}"
    return f"le {d.day} {MONTHS[d.month - 1]} à {d:%H:%M}"


def duration(seconds: float) -> str:
    """« 23 h 41 », « 12 min »."""
    minutes = int(seconds // 60)
    h, m = divmod(minutes, 60)
    return f"{h} h {m:02d}" if h else f"{m} min"


def create_app(config: WebConfig) -> Starlette:
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    def appearance() -> dict:
        """Thème, couleur et taille des miniatures : lus à chaque page (base.html)."""
        with db() as d:
            theme = d.get_setting("ui_theme", "sombre")
            accent = d.get_setting("ui_accent", "jaune")
            thumbs = d.get_setting("ui_thumbs", "moyennes")
        return {"theme": theme if theme in THEMES else "sombre",
                "accent": accent if accent in ACCENTS else "jaune",
                "cols": THUMBS.get(thumbs, THUMBS["moyennes"])[1]}

    templates.env.globals["appearance"] = appearance
    templates.env.filters["human_size"] = human_size
    templates.env.filters["file_name"] = file_name
    templates.env.filters["short_name"] = short_name

    def db() -> Database:
        return Database(config.db_path)

    def scope_from(request: Request, d: Database) -> q.Scope:
        """s=toutes : toutes les photos ; s=1,3 (ou s=1&s=3) : ces sessions (0 : hors session) ;
        sans s : la session active (toutes les photos s'il n'y en a pas)."""
        f = request.query_params.get("f", "toutes")
        raw = ",".join(request.query_params.getlist("s"))
        known = {s.id for s in d.list_sessions()} | {q.NO_SESSION}
        wanted = tuple(sorted({int(x) for x in raw.split(",") if x.strip().isdigit()} & known))
        if raw == "toutes" or (raw and not wanted):
            sessions = None
        elif wanted:
            sessions = wanted
        else:
            active = d.active_session()
            sessions = (active.id,) if active else None
        return q.Scope(f if f in q.FILTERS else "toutes", sessions)

    def storage_summary() -> dict:
        usage = config.pool.usage()
        total = sum(u.total for u in usage)
        used = sum(u.used for u in usage)
        free = config.pool.free_for_photos()
        return {"percent": round(100 * used / total) if total else 0, "free": free,
                "photos_left": free // (50 * 1024 * 1024)}

    def sessions_param(scope: q.Scope) -> str:
        return "toutes" if scope.sessions is None else ",".join(map(str, scope.sessions))

    def query_string(scope: q.Scope) -> str:
        return f"f={scope.filter}&s={sessions_param(scope)}"

    def scope_label(d: Database, scope: q.Scope, active) -> tuple[str, str]:
        """(petite ligne, titre) de la carte « session » en haut de la galerie."""
        if scope.sessions is None:
            return ("Session en cours : " + active.name if active else "Aucune session en cours",
                    "Toutes les photos")
        if len(scope.sessions) == 1:
            sid = scope.sessions[0]
            if sid == q.NO_SESSION:
                return "Sélection", "Photos hors session"
            s = d.get_session(sid)
            return ("Session en cours" if active and active.id == sid else "Session", s.name)
        return "Sélection", f"{len(scope.sessions)} sessions"

    # ------------------------------------------------------------------ pages

    async def gallery(request: Request):
        with db() as d:
            scope = scope_from(request, d)
            active = d.active_session()
            small, title = scope_label(d, scope, active)
            counts = q.session_counts(d)
            ctx = {
                "request": request,
                "scope": scope, "qs": query_string(scope), "s_param": sessions_param(scope),
                "active": active, "scope_small": small, "scope_title": title,
                "all_sessions": [(s, counts.get(s.id, 0)) for s in d.list_sessions()],
                "no_session_count": q.filter_counts(d, (q.NO_SESSION,))["toutes"],
                "status": q.copy_status(d, scope.sessions),
                "storage": storage_summary(),
                "filters": [(k, label, n) for (k, (label, _)), n in
                            zip(q.FILTERS.items(), q.filter_counts(d, scope.sessions).values())],
                "photos": q.photos(d, scope),
                "rejected": q.rejected_count(d),
            }
        return templates.TemplateResponse(request, "gallery.html", ctx)

    async def photo_page(request: Request):
        photo_id = int(request.path_params["photo_id"])
        with db() as d:
            photo = d.get(photo_id)
            if photo is None or not photo.is_saved or photo.trashed_at or photo.purged_at:
                return RedirectResponse("/", status_code=303)
            scope = scope_from(request, d)
            prev_id, next_id, pos, total = q.neighbors(d, scope, photo)
            m = q.media_row(d, photo_id)
            details = {**DEFAULT_DETAILS, **json.loads(d.get_setting("ui_details", "{}"))}
            ctx = {
                "request": request, "photo": photo, "qs": query_string(scope),
                "prev_id": prev_id, "next_id": next_id, "pos": pos, "total": total,
                "file_name": Path(photo.local_path).name,
                "details": details,
                "settings_line": " · ".join(x for x in (
                    f"{m['focal_length']:g} mm" if m and m["focal_length"] else "",
                    f"f/{m['aperture']:g}" if m and m["aperture"] else "",
                    format_exposure(m["exposure_time"]) if m and m["exposure_time"] else "",
                    f"ISO {m['iso']}" if m and m["iso"] else "") if x),
                "gear_line": " · ".join(x for x in ((m["model"] if m else None), (m["lens"] if m else None)) if x),
                "when": french_datetime((m["taken_at"] if m else None) or photo.capture_time),
                "deleted_on_camera": photo.delete_state == "deleted_on_camera",
            }
        return templates.TemplateResponse(request, "photo.html", ctx)

    async def sessions_page(request: Request):
        with db() as d:
            active = d.active_session()
            counts = q.session_counts(d)
            ctx = {
                "request": request, "tab": "sessions", "active": active,
                "active_count": counts.get(active.id, 0) if active else 0,
                "today": datetime.now().date().isoformat(),
                "sessions": [(s, counts.get(s.id, 0)) for s in d.list_sessions()],
                "unsorted": q.filter_counts(d, None)["toutes"] - sum(counts.values()),
            }
        return templates.TemplateResponse(request, "sessions.html", ctx)

    async def reject_page(request: Request):
        with db() as d:
            photos = q.rejected_photos(d)
            ctx = {
                "request": request, "photos": photos,
                "ids": ",".join(str(p.id) for p in photos),
                "size": sum(p.size for p in photos),
                "delay": duration(Trash(d, config.pool).delay.total_seconds()),
            }
        return templates.TemplateResponse(request, "reject.html", ctx)

    async def trash_page(request: Request):
        with db() as d:
            trash = Trash(d, config.pool)
            trash.expire()  # ce qui a dépassé le délai n'est plus récupérable : ne pas le montrer
            items = d.trashed_photos()
            ctx = {
                "request": request,
                "items": [{"photo": p, "name": Path(p.local_path).name, "when": local_time(p.trashed_at)}
                          for p in items],
                "left": duration(min(trash.seconds_left(p) for p in items)) if items else None,
                "restored": request.query_params.get("r"),
            }
        return templates.TemplateResponse(request, "trash.html", ctx)

    async def settings_page(request: Request):
        with db() as d:
            trash = Trash(d, config.pool)
            details = {**DEFAULT_DETAILS, **json.loads(d.get_setting("ui_details", "{}"))}
            usage = config.pool.usage()
            delay = f"{trash.delay.total_seconds() / 3600:g}"
            ctx = {
                "request": request, "tab": "reglages",
                "theme": d.get_setting("ui_theme", "sombre"), "themes": THEMES,
                "accent": d.get_setting("ui_accent", "jaune"), "accents": ACCENTS,
                "thumbs": d.get_setting("ui_thumbs", "moyennes"),
                "thumb_sizes": {k: v[0] for k, v in THUMBS.items()},
                "details": [(k, label, ex, details.get(k, True)) for k, label, ex in DETAILS],
                "auto_delete": d.auto_delete_enabled(),
                "only_on_phone": q.copy_status(d).only_on_phone,
                "storages": [{"label": f"{i}. {storage_label(u.root)}" if len(usage) > 1 else storage_label(u.root),
                              "free": u.free, "total": u.total,
                              "percent": round(100 * u.used / u.total) if u.total else 0}
                             for i, u in enumerate(usage, 1)],
                "photos_left": config.pool.free_for_photos() // (50 * 1024 * 1024),
                "several": len(usage) > 1,
                "rejected": q.rejected_count(d),
                "trashed": len(d.trashed_photos()),
                "delay": delay if delay in TRASH_DELAYS else None, "delays": TRASH_DELAYS,
            }
        return templates.TemplateResponse(request, "settings.html", ctx)

    async def soon_page(request: Request):
        tab = request.url.path.strip("/")
        titles = {"reglages": "Réglages", "journal": "Journal"}
        return templates.TemplateResponse(request, "soon.html", {
            "request": request, "tab": tab, "title": titles.get(tab, "Bientôt")})

    async def not_found(request: Request, exc):
        return templates.TemplateResponse(request, "soon.html", {
            "request": request, "tab": None, "title": "Page introuvable", "missing": True},
            status_code=404)

    # ------------------------------------------------------------------ images

    async def thumb(request: Request):
        with db() as d:
            m = q.media_row(d, int(request.path_params["photo_id"]))
        if m and m["thumb_path"] and Path(m["thumb_path"]).exists():
            return FileResponse(m["thumb_path"], media_type="image/jpeg",
                                headers={"Cache-Control": "max-age=86400"})
        return Response(PLACEHOLDER_SVG, media_type="image/svg+xml")

    def _preview_path(photo_id: int) -> str | None:
        with db() as d:
            m = q.media_row(d, photo_id)
            if m and m["preview_path"] and Path(m["preview_path"]).exists():
                return m["preview_path"]
            if config.indexer_factory is None:
                return m["thumb_path"] if m else None
            with config._lock:  # une génération d'aperçu à la fois (exiftool, mémoire)
                return config.indexer_factory(d).ensure_preview(photo_id)

    async def preview(request: Request):
        from starlette.concurrency import run_in_threadpool
        path = await run_in_threadpool(_preview_path, int(request.path_params["photo_id"]))
        if path and Path(path).exists():
            return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
        return Response(PLACEHOLDER_SVG, media_type="image/svg+xml")

    # ------------------------------------------------------------------ actions

    def guarded(handler):
        async def wrapper(request: Request):
            if request.headers.get("x-cadt") != "1":
                return JSONResponse({"erreur": "requête refusée"}, status_code=403)
            return await handler(request)
        return wrapper

    async def set_favorite(request: Request):
        body = await request.json()
        photo_id = int(request.path_params["photo_id"])
        with db() as d:
            if d.get(photo_id) is None:
                return JSONResponse({"erreur": "photo introuvable"}, status_code=404)
            d.set_favorite(photo_id, bool(body.get("value")))
            return JSONResponse({"favorite": d.get(photo_id).favorite == 1})

    async def set_flag(request: Request):
        body = await request.json()
        flag = body.get("flag")
        if flag not in ("none", "keep", "reject"):
            return JSONResponse({"erreur": "valeur inconnue"}, status_code=400)
        photo_id = int(request.path_params["photo_id"])
        with db() as d:
            if d.get(photo_id) is None:
                return JSONResponse({"erreur": "photo introuvable"}, status_code=404)
            d.set_flag(photo_id, flag)
            return JSONResponse({"flag": d.get(photo_id).flag})

    async def create_session(request: Request):
        body = await request.json()
        name = str(body.get("name", "")).strip()
        if not name:
            return JSONResponse({"erreur": "Donne un nom à la session."}, status_code=400)
        with db() as d:
            session = d.create_session(name)
            d.set_active_session(session.id)
            return JSONResponse({"id": session.id, "folder": session.folder})

    async def set_active(request: Request):
        body = await request.json()
        session_id = body.get("id")
        with db() as d:
            try:
                d.set_active_session(int(session_id) if session_id is not None else None)
            except (ValueError, TypeError):
                return JSONResponse({"erreur": "session introuvable"}, status_code=404)
            active = d.active_session()
            return JSONResponse({"id": active.id if active else None})

    async def move(request: Request):
        body = await request.json()
        try:
            ids = [int(i) for i in body.get("ids", [])]
            dest = body.get("session")
            dest = int(dest) if dest not in (None, "") else None
        except (TypeError, ValueError):
            return JSONResponse({"erreur": "requête invalide"}, status_code=400)
        with db() as d:
            if dest is not None and d.get_session(dest) is None:
                return JSONResponse({"erreur": "session introuvable"}, status_code=404)
            r = move_photos(d, config.pool, ids, dest, rename=bool(body.get("rename")))
            return JSONResponse({"moved": len(r.moved) + len(r.reassigned), "refused": len(r.refused)})

    async def trash_put(request: Request):
        body = await request.json()
        ids = [int(i) for i in body.get("ids", [])]
        with db() as d:
            # Seulement les photos encore rejetées : celles qu'on a « dé-rejetées » entre-temps restent.
            wanted = {p.id for p in q.rejected_photos(d)}
            done = Trash(d, config.pool).put([i for i in ids if i in wanted])
            return JSONResponse({"count": len(done)})

    async def trash_restore(request: Request):
        body = await request.json()
        with db() as d:
            done = Trash(d, config.pool).restore([int(body.get("id", 0))])
            return JSONResponse({"count": len(done)})

    async def trash_empty(request: Request):
        with db() as d:
            trash = Trash(d, config.pool)
            freed = trash.purge([p.id for p in d.trashed_photos()])
            return JSONResponse({"freed": freed})

    async def save_setting(request: Request):
        body = await request.json()
        key, value = body.get("key"), body.get("value")
        with db() as d:
            if key == "theme" and value in THEMES:
                d.set_setting("ui_theme", value)
            elif key == "accent" and value in ACCENTS:
                d.set_setting("ui_accent", value)
            elif key == "thumbs" and value in THUMBS:
                d.set_setting("ui_thumbs", value)
            elif key == "trash_delay" and value in TRASH_DELAYS:
                Trash(d, config.pool).set_delay_hours(float(value))
            elif key == "auto_delete" and isinstance(value, bool):
                d.set_auto_delete(value)
            elif key == "details" and isinstance(value, dict) and set(value) <= {k for k, *_ in DETAILS}:
                current = json.loads(d.get_setting("ui_details", "{}"))
                d.set_setting("ui_details", json.dumps({**current, **{k: bool(v) for k, v in value.items()}}))
            else:
                return JSONResponse({"erreur": "réglage inconnu"}, status_code=400)
        return JSONResponse({"ok": True})

    async def status_api(request: Request):
        with db() as d:
            st = q.copy_status(d)
        return JSONResponse({"unsaved": st.unsaved, "saved": st.saved, "total": st.total,
                             "camera": st.camera, "full": st.full})

    return Starlette(exception_handlers={404: not_found}, routes=[
        Route("/", gallery),
        Route("/sessions", sessions_page),
        Route("/rejet", reject_page),
        Route("/corbeille", trash_page),
        Route("/reglages", settings_page),
        Route("/journal", soon_page),
        Route("/photo/{photo_id:int}", photo_page),
        Route("/media/thumb/{photo_id:int}", thumb),
        Route("/media/preview/{photo_id:int}", preview),
        Route("/api/photo/{photo_id:int}/favorite", guarded(set_favorite), methods=["POST"]),
        Route("/api/photo/{photo_id:int}/flag", guarded(set_flag), methods=["POST"]),
        Route("/api/sessions", guarded(create_session), methods=["POST"]),
        Route("/api/sessions/active", guarded(set_active), methods=["POST"]),
        Route("/api/reglages", guarded(save_setting), methods=["POST"]),
        Route("/api/deplacer", guarded(move), methods=["POST"]),
        Route("/api/corbeille/mettre", guarded(trash_put), methods=["POST"]),
        Route("/api/corbeille/recuperer", guarded(trash_restore), methods=["POST"]),
        Route("/api/corbeille/vider", guarded(trash_empty), methods=["POST"]),
        Route("/api/status", status_api),
        Mount("/static", StaticFiles(directory=str(HERE / "static")), name="static"),
    ])
