from PySide6.QtWidgets import QAbstractButton
from screenkaffee.app import MainWindow
from screenkaffee.editor import EditorWindow
from screenkaffee.parity import FEATURES
from screenkaffee.workspace import Workspace
from PIL import Image


def test_text_recognition_is_removed_from_the_ui(qt_app, tmp_path):
    window = MainWindow(Workspace(tmp_path))
    texts = [button.text() + " " + button.toolTip() for button in window.findChildren(QAbstractButton)]
    assert not any("Text erkennen" in text for text in texts)
    assert not hasattr(window, "ocr_selected")
    states = dict((f[0], f[2]) for f in FEATURES)
    assert states["ocr.text"] == "excluded" and states["ocr.sensitive"] == "excluded"


def test_editor_has_no_sensitive_data_recognition(qt_app):
    from PySide6.QtWidgets import QMenu
    editor = EditorWindow(Image.new("RGB", (100, 60), "white"))
    labels = [a.text() for menu in editor.findChildren(QMenu) for a in menu.actions()]
    assert labels and not any("Sensible Daten" in label for label in labels)
    assert not hasattr(editor, "detect_sensitive")
    assert "Schwärzen" in [a.text() for a in editor.palette_actions.values()] or "redact" in editor.palette_actions
