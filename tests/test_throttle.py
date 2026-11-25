import time

import pytest

from cadt.camera import DirectoryLister
from cadt.downloader import DirectorySource
from cadt.simulator import SimulatedCamera
from cadt.throttle import MB, ThrottledSource


class FakeTime:
    def __init__(self):
        self.t = 0.0

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_rate_is_enforced(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f.NEF").write_bytes(b"x" * (5 * MB))
    ft = FakeTime()
    src = ThrottledSource(DirectorySource(tmp_path, chunk_size=MB), mb_per_s=2.5, per_file_latency=0.3,
                          clock=ft.clock, sleep=ft.sleep)
    data = b"".join(src.iter_chunks("/d", "f.NEF"))
    assert len(data) == 5 * MB
    assert ft.t == pytest.approx(0.3 + 5 / 2.5)


def test_real_time_rate_roughly_respected(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f.NEF").write_bytes(b"x" * (2 * MB))
    src = ThrottledSource(DirectorySource(tmp_path, chunk_size=256 * 1024), mb_per_s=10)
    t = time.monotonic()
    b"".join(src.iter_chunks("/d", "f.NEF"))
    assert 0.18 < time.monotonic() - t < 0.5


def test_invalid_rate():
    with pytest.raises(ValueError):
        ThrottledSource(None, 0)


def test_directory_lister_sees_simulated_two_slot_card(tmp_path):
    sim = SimulatedCamera(tmp_path / "card", raw_size=100, jpeg_size=50, slots=2, slot2_mode="raw_jpeg")
    shot = sim.shoot()
    files = DirectoryLister(tmp_path / "card").list_files()
    assert sorted((f.folder, f.name, f.storage_id) for f in files) == [
        ("/store_00010001/DCIM/100NC850", "DSC_0001.NEF", "store_00010001"),
        ("/store_00020001/DCIM/100NC850", "DSC_0001.JPG", "store_00020001"),
    ]
    assert all(f.capture_time == shot.capture_time.isoformat(timespec="seconds") for f in files)
