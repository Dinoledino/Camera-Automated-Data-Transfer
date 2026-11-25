import json

import pytest

gp = pytest.importorskip("gphoto2")

from cadt import probe  # noqa: E402


def run(tmp_path, *extra):
    return probe.main(["--out", str(tmp_path / "out"), "--card", str(tmp_path / "card"), "--auto",
                       "--shots", "2", "--burst", "0.5", "--sim-raw-size", "200000", *extra])


def state(tmp_path):
    return json.loads((tmp_path / "out" / probe.STATE_FILE).read_text())


def test_full_probe_against_simulator(tmp_path, capsys):
    assert run(tmp_path) == 0
    out = tmp_path / "out"
    s = state(tmp_path)
    assert all(v["status"] == "ok" for v in s["steps"].values())
    assert set(s["steps"]) == {st.id for st in probe.STEPS}
    det = s["steps"]["detection"]["result"]
    assert det["new_files_after_refresh"] == 4 and det["new_files_without_refresh"] == 0
    burst = s["steps"]["burst"]["result"]
    assert burst["verified"] == burst["photos"] > 0
    rec = (out / "config.recommended.toml").read_text()
    assert 'mode = "polling"' in rec and "refresh_each_poll = true" in rec and "partial_reads = false" in rec
    assert "| Rafale réelle et vidange | ✅ |" in (out / "probe_report.md").read_text()
    assert (out / "logs" / "02-detect.log").stat().st_size > 0
    assert not (out / "burst" / "sd").exists()  # la sonde ne garde pas de copies


def test_resume_skips_successful_steps(tmp_path, capsys):
    assert run(tmp_path) == 0
    s = state(tmp_path)
    del s["steps"]["burst"]
    (tmp_path / "out" / probe.STATE_FILE).write_text(json.dumps(s))
    first_detection = s["steps"]["detection"]["ended"]
    capsys.readouterr()

    assert run(tmp_path) == 0
    text = capsys.readouterr().out
    assert "Déclenchement pendant le branchement, détection des nouvelles photos : déjà fait" in text
    assert "(simulateur : rafale" in text and "(simulateur : 2 photos)" not in text
    assert state(tmp_path)["steps"]["detection"]["ended"] == first_detection


def test_from_reruns_following_steps(tmp_path, capsys):
    assert run(tmp_path) == 0
    capsys.readouterr()
    assert run(tmp_path, "--from", "detection") == 0
    text = capsys.readouterr().out
    assert "(simulateur : 2 photos)" in text and "(simulateur : rafale" in text
    assert "Informations système : déjà fait" in text


def test_phone_is_not_taken_for_a_camera(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "autodetect", lambda: [("Samsung Galaxy models (MTP)", "usb:001,008")])
    ctx = probe.Context(tmp_path, probe.Ui(auto=True, out=lambda s: None), probe.Gphoto2Source(), None)
    with pytest.raises(probe.StepFailed, match="aucun appareil photo"):
        probe.step_detect(ctx)
