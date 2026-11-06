"""Kleine Verbesserungen aus dem Review vom 05.11.2026."""
from PIL import Image
from screenkaffee.hotkeys import action_label, display_hotkey


def test_hotkeys_are_shown_with_german_key_names():
    assert display_hotkey("Ctrl+Alt+F8") == "Strg+Alt+F8"
    assert display_hotkey("ctrl+shift+print") == "Strg+Umschalt+Druck"
    assert display_hotkey("Ctrl+Alt+Up") == "Strg+Alt+Pfeil oben"
    assert display_hotkey("Win+s") == "Win+S"
    assert action_label("scroll-finish") == "Scrollserie: fertigstellen"
    assert action_label("unbekannt") == "unbekannt"


def test_hotkey_conflict_names_the_action(qt_app, monkeypatch):
    import sys
    if sys.platform != "win32":
        return
    from screenkaffee.hotkeys import WindowsHotkeys
    keys = WindowsHotkeys({"region": lambda: None, "screen": lambda: None, "window": lambda: None})
    monkeypatch.setattr(keys.ctypes.windll.user32, "RegisterHotKey", lambda *args: 0, raising=False)
    try:
        failed = keys.bind({"screen": "Ctrl+Alt+F8", "window": "Ctrl+Alt+F8", "region": "Ctrl+Blub"})
    finally:
        keys.ids = {}
    assert failed["screen"] == "Bildschirm aufnehmen: Strg+Alt+F8 ist bereits von Windows oder einem anderen Programm belegt."
    assert failed["window"].startswith("Fenster aufnehmen: Strg+Alt+F8 ist in screenkaffee bereits")
    assert failed["region"].startswith("Bereich aufnehmen: Unbekannte Taste")


def test_startup_hotkey_notice_points_to_settings(qt_app, tmp_path):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    window = MainWindow(Workspace(tmp_path))
    text = window.report_hotkey_problems({"scroll-finish": "Scrollserie: fertigstellen: Strg+Alt+F8 ist belegt."})
    assert "Scrollserie: fertigstellen" in text and "Einstellungen → Tastenkürzel" in text
    assert "Strg+Alt+F8" in window.statusBar().currentMessage()


def test_monitor_label_uses_physical_resolution(qt_app):
    from screenkaffee.app import monitor_label

    class Screen:
        def __init__(self, w, h, dpr):
            from PySide6.QtCore import QRect
            self.rect, self.dpr = QRect(0, 0, w, h), dpr

        def geometry(self):
            return self.rect

        def devicePixelRatio(self):
            return self.dpr

    primary, second = Screen(2560, 1440, 1.5), Screen(1920, 1080, 1.0)
    assert monitor_label(0, primary, primary) == "Monitor 1 · 3840×2160 (Hauptbildschirm)"
    assert monitor_label(1, second, primary) == "Monitor 2 · 1920×1080"


def test_delay_box_is_labelled(qt_app, tmp_path):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    window = MainWindow(Workspace(tmp_path))
    assert window.delay.prefix() == "Verzögerung " and window.delay.suffix() == " s"


def test_export_dialogs_suggest_the_capture_title(qt_app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from screenkaffee.app import MainWindow
    from screenkaffee.editor import EditorWindow
    from screenkaffee.workspace import Workspace
    asked = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda parent, caption, name, *a: (asked.append(name), ("", ""))[1])
    workspace = Workspace(tmp_path)
    ident = workspace.add(Image.new("RGB", (20, 20), "white"), title="Rechnung: März/2026")
    window = MainWindow(workspace)
    window.select_id(ident)
    window.export_selected()
    editor = EditorWindow(Image.new("RGB", (20, 20), "white"), "Bug #12?")
    editor.export()
    assert asked[0].startswith("Rechnung_ März_2026.")
    assert asked[1] == "Bug #12_.png"


def test_editor_toolbar_is_compact(qt_app):
    from screenkaffee.editor import EditorWindow
    window = EditorWindow(Image.new("RGB", (200, 100), "white"))
    names = [a.text() for a in window.commandbar.actions() if not a.isSeparator()]
    for moved in ("Rechts drehen", "Links drehen", "Vorheriges Bild", "Nächstes Bild", "Einpassen", "100 %"):
        assert moved not in names
    assert "Drehen" in names and len(names) <= 16
    status = {a.text() for a in window.view_actions}
    assert status == {"Vorheriges Bild", "Nächstes Bild", "Einpassen", "100 %"}
    assert all(a in window.actions() for a in window.view_actions)       # Bild auf/ab bleiben als Kürzel aktiv
    next(a for a in window.commandbar.actions() if a.text() == "Drehen").trigger()
    assert window.view.render_image().size == (100, 200)
