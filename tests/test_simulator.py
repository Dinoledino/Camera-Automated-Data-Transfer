from datetime import datetime, timedelta

import pytest

from cadt.simulator import TMP_DIR_NAME, SimulatedCamera, main


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 25, 14, 0, 0)

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


def cam(tmp_path, **kw):
    kw.setdefault("raw_size", 2000)
    return SimulatedCamera(tmp_path / "card", clock=Clock(), sleep=lambda s: None, **kw)


def names(shot):
    return [f"{f.parent.name}/{f.name}" for f in shot.files]


def test_first_shot_layout_and_pairs(tmp_path):
    c = cam(tmp_path, jpeg_size=500)
    s = c.shoot()
    assert names(s) == ["100NC850/DSC_0001.NEF", "100NC850/DSC_0001.JPG"]
    assert [f.stat().st_size for f in s.files] == [2000, 500]
    assert all(datetime.fromtimestamp(f.stat().st_mtime) == s.capture_time for f in s.files)


def test_files_have_distinct_content(tmp_path):
    c = cam(tmp_path)
    a, b = c.shoot().files[0], c.shoot().files[0]
    assert a.read_bytes() != b.read_bytes()


def test_atomic_mode_leaves_nothing_in_dcim(tmp_path):
    c = cam(tmp_path)
    c.shoot()
    assert list((tmp_path / "card" / TMP_DIR_NAME).iterdir()) == []
    assert [p.name for p in (tmp_path / "card" / "DCIM").rglob("*") if p.is_file()] == ["DSC_0001.NEF"]


def test_new_folder_every_999_files(tmp_path):
    c = cam(tmp_path, raw_size=1)
    shots = [c.shoot() for _ in range(1000)]
    assert names(shots[998]) == ["100NC850/DSC_0999.NEF"]
    assert names(shots[999]) == ["101NC850/DSC_1000.NEF"]


def test_number_wraps_after_9999_in_new_folder(tmp_path):
    folder = tmp_path / "card" / "DCIM" / "105NC850"
    folder.mkdir(parents=True)
    (folder / "DSC_9999.NEF").write_bytes(b"x")
    c = cam(tmp_path)
    assert names(c.shoot()) == ["106NC850/DSC_0001.NEF"]


def test_resumes_numbering_from_card(tmp_path):
    cam(tmp_path).shoot()
    cam(tmp_path).shoot()
    assert names(cam(tmp_path).shoot()) == ["100NC850/DSC_0003.NEF"]


def test_burst_count(tmp_path):
    shots, _ = cam(tmp_path, raw_size=10).burst(seconds=2, fps=7)
    assert len(shots) == 14


def test_cli(tmp_path, capsys):
    assert main([str(tmp_path / "card"), "--shots", "2", "--interval", "0",
                 "--raw-size", "3k", "--jpeg", "1k"]) == 0
    files = sorted(p.name for p in (tmp_path / "card" / "DCIM").rglob("DSC_*"))
    assert files == ["DSC_0001.JPG", "DSC_0001.NEF", "DSC_0002.JPG", "DSC_0002.NEF"]
    assert "2 prises, 4 fichiers" in capsys.readouterr().err


# ------------------------------------------------------------------ avec gphoto2

def test_directory_browse_sees_simulated_card(tmp_path):
    """Le pilote gphoto2 « Directory Browse » lit la carte simulée (après reconnexion)."""
    gp = pytest.importorskip("gphoto2")
    c = cam(tmp_path, jpeg_size=500)
    c.shoot()

    abilities = gp.CameraAbilitiesList()
    abilities.load()
    ports = gp.PortInfoList()
    ports.load()
    camera = gp.Camera()
    camera.set_abilities(abilities.get_abilities(abilities.lookup_model("Directory Browse")))
    camera.set_port_info(ports[ports.lookup_path(f"disk:{tmp_path / 'card'}")])
    camera.init()
    try:
        listed = lambda: sorted(camera.folder_list_files("/DCIM/100NC850").keys())  # noqa: E731
        assert listed() == ["DSC_0001.JPG", "DSC_0001.NEF"]
        c.shoot()
        assert listed() == ["DSC_0001.JPG", "DSC_0001.NEF"]  # cache de libgphoto2
        camera.exit()
        camera.init()
        assert listed() == ["DSC_0001.JPG", "DSC_0001.NEF", "DSC_0002.JPG", "DSC_0002.NEF"]
        data = camera.file_get("/DCIM/100NC850", "DSC_0002.NEF", gp.GP_FILE_TYPE_NORMAL)
        assert bytes(memoryview(data.get_data_and_size())) == (c.card / "DCIM/100NC850/DSC_0002.NEF").read_bytes()
    finally:
        camera.exit()
