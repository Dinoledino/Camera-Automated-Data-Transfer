import shutil
import subprocess
from pathlib import Path

import pytest

from cadt import exif
from cadt.db import Database
from cadt.exif import ExifTool, format_exposure, format_summary, parse

pytestmark = pytest.mark.skipif(not exif.available(), reason="exiftool absent")
Image = pytest.importorskip("PIL.Image")

from cadt.indexer import Indexer  # noqa: E402

SAMPLE_NEF = Path(__file__).parent / "samples" / "D850-14bit-lossless.NEF"


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "cadt.db")
    et = ExifTool()
    yield db, Indexer(db, tmp_path / "sd", et), tmp_path
    et.close()
    db.close()


def saved_photo(db, path, name=None, t="2026-11-25T14:00:00"):
    name = name or Path(path).name
    p, _ = db.add_detected("/DCIM/100NC850", name, Path(path).stat().st_size, t)
    db.claim_next()
    db.mark_downloaded(p.id, "a" * 64)
    return db.mark_verified(p.id, str(path), "a" * 64)


def make_jpeg(path, size=(1200, 800), orientation=1):
    Image.new("RGB", size, (200, 30, 30)).save(path, "JPEG")
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-Model=NIKON D850", "-Make=NIKON CORPORATION",
                    "-DateTimeOriginal=2026:11:25 14:00:00", "-SubSecTimeOriginal=42",
                    "-FNumber=2.8", "-ExposureTime=0.004", "-ISO=400", "-FocalLength=50",
                    f"-Orientation#={orientation}", str(path)], check=True)
    return path


def test_jpeg_metadata_and_images(env):
    db, ix, tmp = env
    photo = saved_photo(db, make_jpeg(tmp / "DSC_0001.JPG"))

    assert ix.run() == 1
    m = ix.get(photo.id)
    assert m.error is None
    assert (m.model, m.iso, m.aperture, m.exposure_time, m.focal_length) == ("NIKON D850", 400, 2.8, 0.004, 50)
    assert m.taken_at == "2026-11-25T14:00:00.42"
    assert Image.open(m.thumb_path).size == (400, 267)
    assert Image.open(m.preview_path).size == (1200, 800)  # jamais agrandie
    assert ix.pending() == []


def test_portrait_orientation_is_applied(env):
    db, ix, tmp = env
    photo = saved_photo(db, make_jpeg(tmp / "DSC_0002.JPG", orientation=6))
    ix.run()
    w, h = Image.open(ix.get(photo.id).thumb_path).size
    assert h > w


def test_broken_file_is_recorded_not_retried(env):
    db, ix, tmp = env
    bad = tmp / "DSC_0003.NEF"
    bad.write_bytes(b"pas une image" * 100)
    photo = saved_photo(db, bad)

    assert ix.run() == 1
    m = ix.get(photo.id)
    assert m.error and m.thumb_path is None
    assert ix.pending() == []  # pas de boucle sur un fichier cassé
    assert "index_error" in [e["kind"] for e in db.events(photo.id)]
    ix.reindex([photo.id])
    assert [p.id for p in ix.pending()] == [photo.id]


def test_only_saved_photos_are_indexed(env):
    db, ix, tmp = env
    make_jpeg(tmp / "DSC_0004.JPG")
    db.add_detected("/DCIM/100NC850", "DSC_0004.JPG", 10, "2026-11-25T14:00:00")
    assert ix.pending() == []


def test_budget_limits_work_per_call(env):
    db, ix, tmp = env
    for i in range(3):
        saved_photo(db, make_jpeg(tmp / f"DSC_00{i}5.JPG"), t=f"2026-11-25T14:00:0{i}")
    assert ix.run(max_photos=2) == 2
    assert len(ix.pending()) == 1


def test_exiftool_restarts_after_crash(env):
    db, ix, tmp = env
    jpg = make_jpeg(tmp / "DSC_0009.JPG")
    ix.exiftool.read(jpg)
    ix.exiftool._proc.kill()
    ix.exiftool._proc.wait()
    assert ix.exiftool.read(jpg)["Model"] == "NIKON D850"  # relancé automatiquement


@pytest.mark.skipif(not SAMPLE_NEF.exists(), reason="NEF d'exemple absent (voir tests/samples/README.md)")
def test_real_d850_nef(env):
    db, ix, tmp = env
    nef = tmp / "DSC_0447.NEF"
    shutil.copy(SAMPLE_NEF, nef)
    photo = saved_photo(db, nef)

    m = ix.index(photo)
    assert m.error is None
    assert (m.model, m.lens, m.iso, m.shutter_count) == ("NIKON D850", "Sigma 35mm F1.4 DG HSM", 64, 447)
    assert (m.width, m.height) == (8288, 5520)
    assert m.taken_at == "2017-10-15T17:34:59.09+01:00"
    assert Image.open(m.thumb_path).size == (400, 265)
    assert Image.open(m.preview_path).size == (2048, 1365)
    assert format_summary(m) == "35 mm · f/9 · 1/160 s · ISO 64"


def test_formatting():
    assert format_exposure(0.00625) == "1/160 s"
    assert format_exposure(2.5) == "2.5 s"
    assert format_exposure(None) is None
    assert format_summary(parse({})) == ""


def test_watcher_indexes_after_downloads(tmp_path):
    from cadt.downloader import DirectorySource, Downloader
    from cadt.watcher import Watcher
    from tests.test_watcher import FakeLister

    card = tmp_path / "card"
    lister = FakeLister(card)
    jpg = make_jpeg(tmp_path / "src.jpg")
    lister.put("DSC_0001.JPG", jpg.read_bytes())
    with Database(tmp_path / "c.db") as db, ExifTool() as et:
        sd = tmp_path / "sd"
        w = Watcher(db, lister, Downloader(db, DirectorySource(card), sd), stable_polls=1,
                    indexer=Indexer(db, sd, et))
        r = w.poll_once()
        assert r.saved == 1 and r.indexed == 1
