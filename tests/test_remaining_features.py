import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from PIL import Image
from PySide6.QtCore import Qt, QRect, QPoint, QPointF
from PySide6.QtTest import QTest


def test_pixel_precision_selection_at_double_scaling(qt_app):
    from screenkaffee.overlay import SelectionOverlay
    image = Image.new("RGB", (400, 300), "red")
    overlay = SelectionOverlay(image, QRect(0, 0, 200, 150))
    result, pixels = [], []
    overlay.regionSelected.connect(result.append)
    overlay.selected.connect(pixels.append)
    overlay.show()
    qt_app.processEvents()
    QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    QTest.mouseMove(overlay, QPoint(60, 50))
    QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, pos=QPoint(60, 50))
    assert not result and overlay.precise_box is not None
    before = overlay.precise_box
    QTest.keyClick(overlay, Qt.Key.Key_Right)
    QTest.keyClick(overlay, Qt.Key.Key_Down, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(overlay, Qt.Key.Key_Return)
    assert result == [(before[0], before[1] + 1, before[2] + 1, before[3] + 1)]
    assert pixels[0].width == before[2] - before[0] + 1


def test_stamp_library_local_import_delete_and_builtin_integrity(tmp_path):
    from screenkaffee.stamps import StampLibrary
    library = StampLibrary(tmp_path / "stamps")
    assert len(library.entries()) == 4
    image = Image.new("RGBA", (40, 20), (25, 50, 75, 128))
    key = library.add(image, "Mein Stempel")
    name, reloaded = library.entries()[key]
    assert name == "Mein Stempel" and reloaded.tobytes() == image.tobytes()
    with pytest.raises(ValueError):
        library.remove("builtin:Erledigt")
    with pytest.raises(ValueError):
        library.remove("../file.png")
    library.remove(key)
    assert len(library.entries()) == 4


def test_tool_defaults_persist_separately_and_apply_to_new_shapes(qt_app, tmp_path, monkeypatch):
    from screenkaffee.presets import save_defaults, load_defaults, defaults_file
    from screenkaffee.editor import EditorView
    monkeypatch.setenv("SCREENKAFFEE_DATA", str(tmp_path))
    styles = {"arrow": {"color": "#123456", "width": 7, "arrow_style": "double", "curve": 15},
              "ellipse": {"color": "#0000ff", "width": 2}}
    save_defaults(styles)
    assert defaults_file().parent == tmp_path
    assert load_defaults() == styles
    view = EditorView(Image.new("RGB", (300, 200), "white"))
    view.set_tool("arrow")
    assert view.width == 7 and view.color == "#123456"
    view.start = QPointF(10, 10)
    data = view.drawing_data(view.start + QPointF(100, 50))
    assert data["arrow_style"] == "double" and data["curve"] == 15
    view.color = "#ff0000"
    view.set_tool("ellipse")
    assert view.color == "#0000ff"
    view.set_tool("arrow")
    assert view.color == "#ff0000"


def test_workspace_filters_sorting_and_legacy_project_compatibility(tmp_path):
    from screenkaffee.workspace import Workspace
    workspace = Workspace(tmp_path)
    first = workspace.add(Image.new("RGB", (40, 20), "red"), "Zulu")
    second = workspace.add(Image.new("RGB", (80, 40), "blue"), "Alpha")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fixture")
    third = workspace.add_file(video, "video", "Film")
    workspace.db.execute("UPDATE captures SET created=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(days=40)).isoformat(), first))
    workspace.db.commit()
    assert [r["id"] for r in workspace.rows(kind="image", sort="title")] == [second, first]
    assert [r["id"] for r in workspace.rows(kind="image", sort="size")] == [second, first]
    assert [r["id"] for r in workspace.rows(kind="video")] == [third]
    assert first not in [r["id"] for r in workspace.rows(days=7)]
    assert workspace.project_path(first).suffixes[-2:] == [".screenkaffee", ".json"]
    legacy = workspace.path(first).with_suffix(".prism.json")
    legacy.write_text('{}')
    assert workspace.project_path(first) == legacy
    current = workspace.path(first).with_suffix(".screenkaffee.json")
    current.write_text('{}')
    assert workspace.project_path(first) == current
    workspace.remove(first)
    workspace.restore(first)
    assert current.exists() and legacy.exists()
    workspace.close()


def test_linux_wheel_modes_and_all_shortcut_actions():
    import tarfile
    from scripts.package_linux import executable_wheels, LAUNCHER
    from scripts.package_windows_arm64 import build_arm64_launcher, ARM64_PACKAGES
    from scripts.verify_release import pe_machine
    from screenkaffee.hotkeys import ACTIONS, DEFAULT_HOTKEYS, parse_hotkey
    info = tarfile.TarInfo("screenkaffee/runtime/lib/python3.12/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64")
    info.mode = 0o644
    assert executable_wheels(info).mode & 0o111
    assert 'export SCREENKAFFEE_APPDIR="$APPDIR"' in LAUNCHER
    assert any(p.startswith("pillow-jxl-plugin") for p in ARM64_PACKAGES)
    assert pe_machine(build_arm64_launcher()) == 0xAA64
    assert {action for action, _ in ACTIONS} == DEFAULT_HOTKEYS.keys()
    for text in DEFAULT_HOTKEYS.values():
        if text:
            parse_hotkey(text)


def test_webcam_options_without_accessing_a_camera(qt_app, monkeypatch):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtGui import QImage, QColor, QPainter
    from screenkaffee import webcam
    class Camera(QObject):
        errorOccurred = Signal(int, str)
        def __init__(self, device, parent):
            super().__init__(parent)
            self.started = False
        def start(self):
            self.started = True
        def stop(self):
            self.started = False
    class Session(QObject):
        def setCamera(self, camera):
            self.camera = camera
        def setVideoSink(self, sink):
            self.sink = sink
    monkeypatch.setattr(webcam, "QCamera", Camera)
    monkeypatch.setattr(webcam, "QMediaCaptureSession", Session)
    overlay = webcam.WebcamOverlay(None, size=120, circle=False, mirror=True, position="top-left")
    overlay.place("top-left", QRect(100, 100, 800, 600))
    assert overlay.pos() == QPoint(124, 124)
    image = QImage(120, 120, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    painter = QPainter(image)
    painter.fillRect(60, 0, 60, 120, QColor("blue"))
    painter.end()
    overlay.image = image
    overlay.start()
    qt_app.processEvents()
    preview = overlay.grab().toImage()
    assert preview.pixelColor(30, 60).blue() > 220
    assert preview.pixelColor(90, 60).red() > 220
    assert overlay.camera.started
    overlay.close()
    assert not overlay.camera.started


def test_duplicate_shortcuts_are_rejected_without_second_registration(qt_app):
    from types import SimpleNamespace
    from screenkaffee.hotkeys import WindowsHotkeys
    registered = []
    user = SimpleNamespace(RegisterHotKey=lambda *args: registered.append(args) or 1, UnregisterHotKey=lambda *args: 1)
    keys = WindowsHotkeys({"region": lambda: None, "window": lambda: None})
    keys.ctypes = SimpleNamespace(windll=SimpleNamespace(user32=user))
    problems = keys.bind({"region": "Ctrl+A", "window": "Strg+A"})
    assert set(problems) == {"window"}
    assert len(registered) == 1
    keys.close()
