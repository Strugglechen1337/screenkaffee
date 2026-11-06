import subprocess
import pytest
from PySide6.QtWidgets import QFileDialog
from screenkaffee.media import ffmpeg_binary, probe_media


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "quelle.mp4"
    subprocess.run([ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=d=1:s=320x180:r=15",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True, timeout=120)
    return path


def run_export(editor, qt_app, monkeypatch, target):
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    editor.export_video()
    editor.job.wait(60000)
    for _ in range(50):
        qt_app.processEvents()


def test_editor_exports_selected_size_format_and_quality(qt_app, source, tmp_path, monkeypatch):
    from screenkaffee.media_editor import VideoEditor
    editor = VideoEditor(source)
    editor.size_box.setCurrentIndex(editor.size_box.findText("640 × 360"))
    editor.resize_mode.setCurrentIndex(editor.resize_mode.findData("fitmax"))
    editor.quality.setCurrentIndex(editor.quality.findData("low"))
    run_export(editor, qt_app, monkeypatch, tmp_path / "ziel.mkv")
    info = probe_media(tmp_path / "ziel.mkv")
    assert (info.width, info.height) == (640, 360)
    editor.close()


def test_editor_exports_an_html5_page(qt_app, source, tmp_path, monkeypatch):
    from screenkaffee.media_editor import VideoEditor
    editor = VideoEditor(source)
    run_export(editor, qt_app, monkeypatch, tmp_path / "seite.html")
    assert (tmp_path / "seite.html").exists() and (tmp_path / "seite_dateien" / "quelle.mp4").exists()
    editor.close()
