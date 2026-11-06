from datetime import datetime
import pytest
from PIL import Image
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication, QMessageBox
from screenkaffee import capture_options as co


def test_title_pattern_supports_dates_and_running_numbers():
    now = datetime(2026, 10, 28, 14, 5, 9)
    assert co.title_for("Aufnahme %d.%m.%Y %H:%M:%S", now=now) == "Aufnahme 28.10.2026 14:05:09"
    assert co.title_for("Shot_{n}_%Y%m%d", number=7, now=now) == "Shot_7_20261028"
    assert co.title_for("", now=now) == "Aufnahme 28.10.2026 14:05:09"
    assert len(co.title_for("x" * 500, now=now)) == 200


def test_validation_names_each_bad_value():
    assert co.validate(dict(co.DEFAULTS)) == {}
    bad = dict(co.DEFAULTS, title_pattern="  ", export_format=".exe", auto_scale_percent=2, auto_border=True, animation_fps=0)
    assert set(co.validate(bad)) == {"title_pattern", "export_format", "auto_scale_percent", "auto_border", "animation_fps"}


def test_post_processing_applies_only_enabled_steps():
    image = Image.new("RGB", (100, 60), "white")
    assert co.post_process(image, dict(co.DEFAULTS)) is image
    half = co.post_process(image, dict(co.DEFAULTS, auto_scale_percent=50))
    assert half.size == (50, 30)
    framed = co.post_process(image, dict(co.DEFAULTS, auto_border=4))
    assert framed.size == (100, 60) and framed.getpixel((0, 30)) != (255, 255, 255)
    shadowed = co.post_process(image, dict(co.DEFAULTS, auto_shadow=5))
    assert shadowed.width > 100 and shadowed.height > 60


def test_print_area_applies_millimetre_margins_and_never_collapses():
    from screenkaffee.printing import print_area
    area = print_area(QRectF(0, 0, 2100, 2970), 254, 254, 10)      # 10 mm = 100 px at 254 dpi
    assert (area.x(), area.y(), area.width(), area.height()) == (100, 100, 1900, 2770)
    assert print_area(QRectF(0, 0, 200, 200), 254, 254, 0).width() == 200
    assert print_area(QRectF(0, 0, 200, 200), 254, 254, 50).width() >= 20


def make_window(tmp_path, **settings):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    window = MainWindow(Workspace(tmp_path))
    window.settings.update(auto_edit=False, auto_copy=False, **settings)
    return window


def test_new_captures_use_the_title_pattern_and_automatic_effects(qt_app, tmp_path):
    window = make_window(tmp_path, title_pattern="Shot_{n}", auto_scale_percent=50)
    window.finish_capture(Image.new("RGB", (200, 100), "red"), {"backend": "test"})
    window.finish_capture(Image.new("RGB", (200, 100), "blue"), {"backend": "test"})
    rows = window.workspace.rows(sort="oldest")
    assert [r["title"] for r in rows] == ["Shot_1", "Shot_2"]
    assert (rows[0]["width"], rows[0]["height"]) == (100, 50)


def test_clipboard_only_mode_does_not_store_the_capture(qt_app, tmp_path):
    window = make_window(tmp_path, clipboard_only=True)
    QApplication.clipboard().clear()
    window.finish_capture(Image.new("RGB", (64, 32), "green"), {"backend": "test"})
    assert window.workspace.rows() == []
    assert QApplication.clipboard().image().width() == 64


def test_remove_asks_only_when_confirmation_is_enabled(qt_app, tmp_path, monkeypatch):
    window = make_window(tmp_path, confirm_delete=False)
    window.finish_capture(Image.new("RGB", (20, 20), "red"), {"backend": "test"})
    window.list.item(0).setSelected(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("Es darf nicht nachgefragt werden"))
    window.remove_selected()
    assert window.workspace.rows() == []
    window.finish_capture(Image.new("RGB", (20, 20), "red"), {"backend": "test"})
    window.settings["confirm_delete"] = True
    window.list.item(0).setSelected(True)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.No)
    window.remove_selected()
    assert asked == [1] and len(window.workspace.rows()) == 1


def test_preferences_dialog_keeps_and_saves_the_new_options(qt_app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog
    window = make_window(tmp_path, title_pattern="Bild_{n}", auto_border=3, clipboard_only=True)
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(window, "apply_hotkeys", lambda: {})        # never register real global hotkeys in a test
    from screenkaffee import autostart
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    monkeypatch.setattr(autostart, "set_enabled", lambda enabled: pytest.fail("Autostart darf im Test nicht verändert werden"))
    window.preferences()
    assert (window.settings["title_pattern"], window.settings["auto_border"], window.settings["clipboard_only"]) == ("Bild_{n}", 3, True)
    assert window.settings["export_format"] == ".png" and window.settings["confirm_delete"] is True
    assert '"title_pattern": "Bild_{n}"' in window.settings_path.read_text(encoding="utf-8")


def test_animation_loops_reach_the_encoders():
    from screenkaffee.media import _codec_options
    assert _codec_options(".apng", loops=3)[-2:] == ["-plays", "3"]
    assert _codec_options(".webp", loops=2)[3] == "2"


def test_capture_context_and_placeholders(qt_app, tmp_path, monkeypatch):
    import json
    now = datetime(2026, 10, 28, 14, 5, 9)
    ctx = {"window_title": "Ticket #42 - https://example.org/issues/42", "process_name": "firefox", "url": "https://example.org/issues/42"}
    assert co.extract_url(ctx["window_title"]) == "https://example.org/issues/42"
    title = co.title_for("{app}_{window}_{n}", number=3, now=now, context=ctx)
    assert title.startswith("firefox_Ticket #42") and title.endswith("_3")
    monkeypatch.setattr(co, "collect_capture_context", lambda: dict(ctx))
    window = make_window(tmp_path, title_pattern="{app}_{n}", capture_context=True)
    window.finish_capture(Image.new("RGB", (40, 30), "red"), {"backend": "test"})
    row = window.workspace.rows()[0]
    meta = json.loads(row["metadata"])
    assert row["title"] == "firefox_1"
    assert meta["process_name"] == "firefox" and meta["url"] == "https://example.org/issues/42"
