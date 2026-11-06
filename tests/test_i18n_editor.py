"""Englische Editoroberfläche einschließlich dynamischer Dialogtexte."""
import pytest
from PIL import Image
from PySide6.QtCore import QRect
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog, QLabel, QPushButton

from screenkaffee import i18n


@pytest.fixture
def english():
    i18n.set_language("en")
    yield
    i18n.set_language("de")


def test_editor_english_actions_and_dialogs(qt_app, english, monkeypatch):
    from screenkaffee.editor import EditorWindow, Annotation
    window = EditorWindow(Image.new("RGB", (400, 240), "white"))
    assert window.windowTitle().startswith("Capture — Editor")
    assert window.tool_combo.itemText(0) == "Select / Move"
    assert window.palette_actions["redact"].toolTip() == "Redact"
    assert window.rotate_menu.title() == "Rotate"
    assert any(a.text() == "Copy && Close" for a in window.findChildren(QAction))
    assert "Alt+Shift" in window.view_actions[0].toolTip()
    assert window.arrange_menu.actions()[0].menu().actions()[0].text().startswith("Bring to front")
    dialogs = []

    def inspect(dialog):
        dialogs.append((dialog.windowTitle(), [w.text() for w in dialog.findChildren(QLabel)]))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", inspect)
    window.image_effects()
    window.canvas_dialog()
    window.border_dialog()
    window.cut_out_dialog()
    annotation = Annotation({"kind": "magnifier", "points": [0, 0, 100, 80], "color": "#ff0000", "width": 3}, window.view)
    window.view.scene().addItem(annotation)
    annotation.setSelected(True)
    window.annotation_properties()
    assert [title for title, _ in dialogs] == ["Image size, border, and effects", "Canvas size", "Border", "Cut out area", "Edit annotation"]
    assert "Watermark position" in dialogs[0][1]
    assert "Text alignment" in dialogs[-1][1]
    preview = window.preview_output()
    qt_app.processEvents()
    assert any(w.text().startswith("Output size:") for w in preview.findChildren(QLabel))
    window.discard_changes = True


def test_beautify_compare_and_richtext_english(qt_app, english):
    from screenkaffee.beautify import beautify_dialog
    from screenkaffee.compare import CompareWindow, Difference
    from screenkaffee.richtext import RichTextDialog
    image = Image.new("RGB", (320, 180), "#d0def0")
    beautify = beautify_dialog(None, image)
    assert beautify.windowTitle() == "Beautify"
    assert beautify.style.itemText(0) == "Coffee"
    assert beautify.frame.itemText(0) == "No window frame"
    assert beautify.size_label.text().startswith("Result:")
    assert beautify.copy_button.text() == "Copy to clipboard"
    compare = CompareWindow(image, Image.new("RGB", image.size, "red"))
    assert compare.windowTitle() == "Compare: Before ↔ After"
    assert compare.mode.itemText(1) == "Blend"
    assert "regions changed" in Difference(image.size, .1, [(0, 0, 1, 1), (2, 2, 3, 3)]).summary()
    assert "region changed" in Difference(image.size, .1, [(0, 0, 1, 1)]).summary()
    richtext = RichTextDialog(None, {"text": "Benutzertext", "color": "#000000"})
    assert richtext.windowTitle() == "Edit text"
    assert richtext.editor.toPlainText() == "Benutzertext"
    assert "Set size" in [b.text() for b in richtext.findChildren(QPushButton)]


def test_capture_helpers_english(qt_app, english):
    from screenkaffee.overlay import SelectionOverlay
    from screenkaffee.regionadjust import RegionAdjuster
    from screenkaffee.livedraw import LiveDrawWindow, DrawState
    from screenkaffee.pin import PinnedImage
    from screenkaffee.ruler import label, clipboard_text, measurement
    image = Image.new("RGB", (800, 600), "#c0c8d0")
    overlay = SelectionOverlay(image, QRect(0, 0, 800, 600))
    overlay.show_help = True
    overlay.grab()
    adjuster = RegionAdjuster([10, 10, 400, 240], geometry=QRect(0, 0, 800, 600))
    assert adjuster.start_button.text() == "● Start recording"
    assert adjuster.size_box.itemText(0) == "Free"
    pin = PinnedImage(image)
    assert pin.windowTitle() == "Pinned"
    live = LiveDrawWindow(qt_app.primaryScreen(), DrawState(), None)
    live.grab()
    assert "W 30" in label(measurement(0, 0, 30, 40))
    assert "length 50 px" in clipboard_text(measurement(0, 0, 30, 40))


def test_builtin_names_translate_but_user_names_and_ids_stay(qt_app, english, tmp_path):
    from screenkaffee.templates import TemplateLibrary, builtin_templates
    from screenkaffee.stamps import builtin_stamps
    library = TemplateLibrary(tmp_path)
    items = builtin_templates()[0]["items"]
    assert builtin_templates()[0]["name"] == "Click here"
    assert items[1]["text"] == "Click here"
    library.add("Meine Vorlage", items)
    assert library.entries()[-1]["name"] == "Meine Vorlage"
    assert builtin_stamps()["builtin:Erledigt"][0] == "Done"
    i18n.set_language("de")
    assert builtin_templates()[0]["name"] == "Hier klicken"
    assert library.entries()[-1]["name"] == "Meine Vorlage"


def test_editor_errors_and_stamp_dialog_english(qt_app, english, monkeypatch):
    from screenkaffee import stamps
    from screenkaffee.color_picker import color_formats
    from screenkaffee.editor import EditorWindow
    image = Image.new("RGB", (320, 180), "white")
    seen = []

    def inspect(dialog):
        seen.append((dialog.windowTitle(), [b.text() for b in dialog.findChildren(QPushButton)]))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", inspect)
    assert stamps.choose_stamp(None) is None
    assert seen[-1][0] == "Stamp library"
    assert "Add custom stamp" in seen[-1][1]
    window = EditorWindow(image)
    messages = []
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text: messages.append((title, text)))
    window.image_properties()
    assert messages[0][0] == "Image properties"
    assert "Project file: not saved yet" in messages[0][1]
    assert "Annotations: 0" in messages[0][1]
    with pytest.raises(ValueError, match="Invalid RGB color value"):
        color_formats("invalid")
    window.discard_changes = True
