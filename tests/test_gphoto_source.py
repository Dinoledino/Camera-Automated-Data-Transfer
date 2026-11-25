import pytest

gp = pytest.importorskip("gphoto2")

from cadt import gphoto_source  # noqa: E402
from cadt.gphoto_source import Gphoto2Source  # noqa: E402


def test_finds_camera_on_new_usb_port(monkeypatch):
    """Après un redémarrage de l'appareil, son port USB change : on le retrouve par son modèle."""
    monkeypatch.setattr(gphoto_source, "autodetect", lambda: [
        ("Samsung Galaxy models (MTP)", "usb:001,008"), ("Nikon DSC D850", "usb:001,012")])
    s = Gphoto2Source("Nikon DSC D850", "usb:001,009")
    assert s._find_moved_port() == "usb:001,012"


def test_no_moved_port_for_other_models_or_disk(monkeypatch):
    monkeypatch.setattr(gphoto_source, "autodetect", lambda: [("Samsung Galaxy models (MTP)", "usb:001,008")])
    assert Gphoto2Source("Nikon DSC D850", "usb:001,009")._find_moved_port() is None
    assert Gphoto2Source.directory("/tmp/x")._find_moved_port() is None
