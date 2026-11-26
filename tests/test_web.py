"""Interface de tri : galerie, plein écran, actions."""
import re

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")
pytest.importorskip("jinja2")

from starlette.testclient import TestClient  # noqa: E402

from cadt import exif  # noqa: E402
from cadt.camera import DirectoryLister  # noqa: E402
from cadt.cleaner import CameraCleaner  # noqa: E402
from cadt.db import Database  # noqa: E402
from cadt.downloader import DirectorySource, Downloader  # noqa: E402
from cadt.simulator import SimulatedCamera  # noqa: E402
from cadt.storage import StoragePool  # noqa: E402
from cadt.trash import Trash  # noqa: E402
from cadt.watcher import Watcher  # noqa: E402
from cadt.web.app import WebConfig, create_app  # noqa: E402

JARGON = re.compile(r"SHA|hash|microSD|checksum|PTP|(?<!\.)\bNEF\b", re.I)  # « .NEF » d'un nom de fichier : permis
H = {"X-CADT": "1"}


@pytest.fixture
def env(tmp_path):
    card, sd = tmp_path / "card", tmp_path / "sd"
    pool = StoragePool([sd], reserve_bytes=0)
    sim = SimulatedCamera(card, raw_size=3000, slots=2)
    with Database(tmp_path / "c.db") as db:
        src = DirectorySource(card)
        w = Watcher(db, DirectoryLister(card), Downloader(db, src, pool), stable_polls=1,
                    cleaner=CameraCleaner(db, src))
        for _ in range(3):
            sim.shoot()
        w.poll_once()
        w._status("connecté")
        ids = [r[0] for r in db.conn.execute("SELECT id FROM photos ORDER BY id")]
    client = TestClient(create_app(WebConfig(tmp_path / "c.db", pool)))
    yield client, ids, tmp_path, pool


def text(html):
    """Texte visible (sans balises ni attributs techniques)."""
    html = re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def test_gallery(env):
    client, ids, tmp, _ = env
    r = client.get("/")
    assert r.status_code == 200
    t = text(r.text)
    assert "Appareil photo branché" in t
    assert "Toutes les photos sont à l'abri" in t
    assert "3 sur 3 copiées et contrôlées, puis effacées de l'appareil photo" in t
    assert "Mémoire du téléphone" in t
    for i in ids:
        assert f'href="/photo/{i}?' in r.text
    assert not JARGON.search(t), JARGON.search(t)


def test_photo_page_navigation(env):
    client, ids, tmp, _ = env
    newest, middle, oldest = ids[2], ids[1], ids[0]   # la galerie montre les plus récentes d'abord
    r = client.get(f"/photo/{middle}?f=toutes&s=toutes")
    assert r.status_code == 200
    assert f'data-prev="/photo/{newest}?' in r.text and f'data-next="/photo/{oldest}?' in r.text
    t = text(r.text)
    assert "2 sur 3" in t and "Mettre en favori" in t
    assert not JARGON.search(t), JARGON.search(t)


def test_actions_require_header(env):
    client, ids, tmp, _ = env
    assert client.post(f"/api/photo/{ids[0]}/favorite", json={"value": True}).status_code == 403
    r = client.post(f"/api/photo/{ids[0]}/favorite", json={"value": True}, headers=H)
    assert r.json() == {"favorite": True}
    r = client.get("/?f=favoris&s=toutes")
    assert f'/photo/{ids[0]}?' in r.text and f'/photo/{ids[1]}?' not in r.text


def test_reject_shows_banner_and_filter(env):
    client, ids, tmp, _ = env
    assert client.post(f"/api/photo/{ids[1]}/flag", json={"flag": "reject"}, headers=H).json() == {"flag": "reject"}
    t = text(client.get("/").text)
    assert "1 photo rejetée" in t and "Supprimer du téléphone" in t
    assert client.post(f"/api/photo/{ids[1]}/flag", json={"flag": "n'importe"}, headers=H).status_code == 400


def test_trashed_photo_is_hidden(env):
    client, ids, tmp, pool = env
    with Database(tmp / "c.db") as db:
        Trash(db, pool).put([ids[0]])
    assert f"/photo/{ids[0]}?" not in client.get("/").text
    r = client.get(f"/photo/{ids[0]}", follow_redirects=False)
    assert r.status_code == 303


def test_thumb_placeholder_then_real(env):
    client, ids, tmp, pool = env
    r = client.get(f"/media/thumb/{ids[0]}")
    assert r.headers["content-type"].startswith("image/svg")


@pytest.mark.skipif(not exif.available(), reason="exiftool absent")
def test_preview_on_demand(env):
    pytest.importorskip("PIL")
    import io
    import subprocess
    from PIL import Image
    from cadt.indexer import Indexer
    client, ids, tmp, pool = env
    # Remplace la première photo par un vrai JPEG, puis l'indexe (miniature seulement).
    with Database(tmp / "c.db") as db:
        p = db.get(ids[0])
        jpg = tmp / "vrai.jpg"
        Image.new("RGB", (900, 600), (90, 120, 150)).save(jpg, "JPEG")
        subprocess.run(["exiftool", "-q", "-overwrite_original", "-FNumber=4", "-ISO=200",
                        "-FocalLength=85", "-ExposureTime=0.002", str(jpg)], check=True)
        jpg.replace(p.local_path)
        db.conn.execute("UPDATE photos SET kind = 'jpeg' WHERE id = ?", (p.id,))
    et = exif.ExifTool()
    try:
        factory = lambda db: Indexer(db, pool.primary, et)  # noqa: E731
        with Database(tmp / "c.db") as db:
            factory(db).run(max_photos=1)
        client = TestClient(create_app(WebConfig(tmp / "c.db", pool, factory)))
        assert client.get(f"/media/thumb/{ids[0]}").headers["content-type"] == "image/jpeg"
        r = client.get(f"/media/preview/{ids[0]}")
        assert r.headers["content-type"] == "image/jpeg"
        assert Image.open(io.BytesIO(r.content)).size == (900, 600)
        assert "85 mm · f/4 · 1/500 s · ISO 200" in client.get(f"/photo/{ids[0]}").text
    finally:
        et.close()


def test_status_api(env):
    client, ids, tmp, _ = env
    assert client.get("/api/status").json() == {"unsaved": 0, "saved": 3, "total": 3,
                                                 "camera": "branché", "full": False}


def test_no_link_leads_to_an_error(env):
    """Chaque lien de chaque écran mène à une page qui répond (jamais d'erreur brute)."""
    client, ids, tmp, _ = env
    client.post(f"/api/photo/{ids[1]}/flag", json={"flag": "reject"}, headers=H)
    seen, todo = set(), ["/", "/sessions", "/rejet", "/corbeille", "/reglages", "/journal"]
    while todo:
        url = todo.pop()
        if url in seen:
            continue
        seen.add(url)
        r = client.get(url)
        assert r.status_code == 200, url
        if "text/html" in r.headers["content-type"]:
            assert not JARGON.search(text(r.text)), (url, JARGON.search(text(r.text)))
            todo += [h.replace("&amp;", "&") for h in re.findall(r'href="(/[^"]*)"', r.text)
                     if not h.startswith(("/static", "/media"))]
    r = client.get("/nimporte-quoi")
    assert r.status_code == 404 and "Cette page n'existe pas" in r.text


def test_sessions_create_activate_stop(env):
    client, ids, tmp, _ = env
    assert "Aucune" in text(client.get("/sessions").text)
    assert client.post("/api/sessions", json={"name": "Rando Vercors"}).status_code == 403
    assert client.post("/api/sessions", json={"name": "  "}, headers=H).status_code == 400
    sid = client.post("/api/sessions", json={"name": "Rando Vercors"}, headers=H).json()["id"]
    t = text(client.get("/sessions").text)
    assert "Rando Vercors" in t and "Prochaine photo : n° 0001" in t
    assert "Session active" in text(client.get("/").text)
    other = client.post("/api/sessions", json={"name": "Studio"}, headers=H).json()["id"]
    assert client.post("/api/sessions/active", json={"id": sid}, headers=H).json() == {"id": sid}
    assert client.post("/api/sessions/active", json={"id": 999}, headers=H).status_code == 404
    assert client.post("/api/sessions/active", json={"id": None}, headers=H).json() == {"id": None}
    with Database(tmp / "c.db") as db:
        assert db.active_session() is None and {s.id for s in db.list_sessions()} == {sid, other}


def test_reject_then_trash_then_restore(env):
    client, ids, tmp, pool = env
    client.post(f"/api/photo/{ids[0]}/flag", json={"flag": "reject"}, headers=H)
    client.post(f"/api/photo/{ids[1]}/flag", json={"flag": "reject"}, headers=H)
    t = text(client.get("/rejet").text)
    assert "2 photos" in t and "plus sur l'appareil photo" in t and "24 h 00" in t
    # ids[1] « dé-rejetée » entre l'affichage et la confirmation : elle n'est pas supprimée.
    client.post(f"/api/photo/{ids[1]}/flag", json={"flag": "none"}, headers=H)
    r = client.post("/api/corbeille/mettre", json={"ids": [ids[0], ids[1]]}, headers=H)
    assert r.json() == {"count": 1}
    with Database(tmp / "c.db") as db:
        p = db.get(ids[0])
        assert p.in_trash and ".cadt/corbeille" in p.local_path
        assert not db.get(ids[1]).trashed_at
    t = text(client.get("/corbeille").text)
    assert "Récupérables encore" in t and "Récupérer" in t
    assert client.post("/api/corbeille/recuperer", json={"id": ids[0]}, headers=H).json() == {"count": 1}
    assert f"/photo/{ids[0]}?" in client.get("/").text
    assert "La corbeille est vide" in text(client.get("/corbeille?r=1").text)


def test_empty_trash(env):
    client, ids, tmp, pool = env
    client.post(f"/api/photo/{ids[2]}/flag", json={"flag": "reject"}, headers=H)
    client.post("/api/corbeille/mettre", json={"ids": [ids[2]]}, headers=H)
    with Database(tmp / "c.db") as db:
        path = db.get(ids[2]).local_path
    assert client.post("/api/corbeille/vider", json={}, headers=H).json()["freed"] == 3000
    with Database(tmp / "c.db") as db:
        assert db.get(ids[2]).purged_at
    from pathlib import Path
    assert not Path(path).exists()
    assert "La corbeille est vide" in text(client.get("/corbeille").text)
