import argparse
from pathlib import Path

import pytest
from PIL import Image
from screenkaffee import cli, policy
from screenkaffee.workspace import ThumbnailCache


@pytest.mark.parametrize("command", [
    ["capture", "--screenshotregion", "--rect", "0;0;20;20"],
    ["capture", "--videoregion", "--rect", "0;0;20;20"],
    ["videos", "--merge", "a.mp4", "b.mp4"],
])
def test_richtlinien_sperren_cli_vor_aufnahme(monkeypatch, capsys, command):
    monkeypatch.setattr(policy, "load_policy", lambda: {"disabled": ["capture", "video"], "locked": {}})
    def verboten(*args, **kwargs):
        pytest.fail("Gesperrter Recorder darf nicht starten")
    monkeypatch.setattr("screenkaffee.integrations.VideoRecorder", verboten)
    monkeypatch.setattr("screenkaffee.capture.capture_region", verboten)
    assert cli.dispatch(command) == 1
    assert "Richtlinie gesperrt" in capsys.readouterr().err


def test_gesperrte_bildeinstellungen_gelten(monkeypatch, tmp_path):
    monkeypatch.setattr(policy, "load_policy", lambda: {"disabled": [], "locked": {"tone_mapping": "highlights", "capture_cursor": True}})
    seen = []
    def aufnehmen(bounds, tone, cursor):
        seen.append((tone, cursor))
        return Image.new("RGB", (20, 20)), {}
    monkeypatch.setattr("screenkaffee.capture.capture_region", aufnehmen)
    cli.run_capture(["--screenshotregion", "--rect", "0;0;20;20", "--filepath", str(tmp_path / "bild.png")])
    assert seen == [("highlights", True)]


@pytest.mark.parametrize("failure", ["start", "poll", "copy", None])
def test_video_stoppt_und_bewahrt_fehlerdaten(tmp_path, monkeypatch, capsys, failure):
    import tempfile
    folder = tmp_path / "roh"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **kw: str(folder))
    class Recorder:
        stopped = 0
        def start(self, folder, **kw):
            (folder / "film.mp4").write_bytes(b"video")
            if failure == "start":
                raise RuntimeError("Startfehler")
        def error_if_stopped(self):
            if failure == "poll":
                raise RuntimeError("Aufnahmefehler")
        def stop(self):
            self.stopped += 1
            return folder / "film.mp4"
    recorder = Recorder()
    target = tmp_path / "aus.mp4"
    if failure == "copy":
        target.mkdir()
        (target / "film.mp4").mkdir()
    args = argparse.Namespace(rect="0;0;20;20", delay=.01, filepath=target)
    ticks = iter([0, 0, 0, 1])
    run = lambda: cli.run_video(args, lambda: recorder, lambda seconds: None, ticks.__next__)
    if failure:
        with pytest.raises((RuntimeError, OSError)):
            run()
        assert folder.exists()
        assert str(folder) in capsys.readouterr().err
    else:
        assert run() == 0
        assert not folder.exists()
    assert recorder.stopped == 1


def test_miniaturcache_treffer_ungueltig_und_begrenzt(tmp_path, monkeypatch):
    source = tmp_path / "bild.png"
    Image.new("RGB", (1200, 800)).save(source)
    cache = ThumbnailCache(tmp_path, limit=2)
    first = cache.get(source)
    with Image.open(first) as image:
        assert image.width <= 344 and image.height <= 236
    original = Image.open
    def verboten(*args, **kwargs):
        pytest.fail("Cachetreffer darf Original nicht dekodieren")
    monkeypatch.setattr(Image, "open", verboten)
    assert cache.get(source) == first
    monkeypatch.setattr(Image, "open", original)
    Image.new("RGB", (500, 200), "red").save(source)
    assert cache.get(source) != first
    Image.new("RGB", (100, 100), "blue").save(source)
    cache.get(source)
    assert len(list(cache.root.glob("*.png"))) == 2


def test_refresh_dekodiert_originale_verzoegert(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "apply_hotkeys", lambda self: None)
    workspace = Workspace(tmp_path)
    for _ in range(30):
        workspace.add(Image.new("RGB", (10, 10)))
    window = MainWindow(workspace)
    ident = workspace.rows()[0]["id"]
    window.select_id(ident)
    monkeypatch.setattr(ThumbnailCache, "get", lambda *args: pytest.fail("refresh darf kein Original dekodieren"))
    window.refresh()
    assert window.selected_ids() == [ident]
    assert len(window.thumbnail_loader.jobs) == 30
    window.thumbnail_loader.reset()
    window.close()
    workspace.close()
