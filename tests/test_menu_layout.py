"""Aufgeräumtes „Mehr“-Menü, Aufklappmenü am Video-Knopf, Knopf „Teilen“ und gruppierte Editor-Werkzeuge."""
from PIL import Image


def window_for(tmp_path, monkeypatch):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    monkeypatch.setattr(MainWindow, "apply_hotkeys", lambda self: {})
    return MainWindow(Workspace(tmp_path))


def labels(menu):
    return [action.text() for action in menu.actions() if not action.isSeparator()]


def more_menu(window):
    from PySide6.QtWidgets import QToolButton
    return next(b for b in window.findChildren(QToolButton) if b.text() == "Mehr").menu()


def test_more_menu_is_grouped_into_submenus(qt_app, tmp_path, monkeypatch):
    window = window_for(tmp_path, monkeypatch)
    menu = more_menu(window)
    assert labels(menu) == ["Aufnehmen", "Video", "Auswahl bearbeiten", "Teilen und ausgeben", "Sammlungen und Daten",
                            "Hilfe und Updates", "Auswahl entfernen …"]
    submenus = {action.text(): action.menu() for action in menu.actions() if action.menu()}
    assert len(submenus) == 6 and all(len(labels(sub)) >= 4 for sub in submenus.values())
    everything = [text for sub in submenus.values() for text in labels(sub)]
    for text in ("Zeitraffer starten …", "Zwei Aufnahmen vergleichen …", "Aufs Handy senden (QR-Code) …",
                 "Schicker Screenshot (Hintergrund, Rahmen) …", "Webseite komplett aufnehmen …", "Nach Updates suchen …",
                 "Gelöschte Aufnahmen wiederherstellen …", "Android-Video starten", "Fehlerprotokoll öffnen"):
        assert text in everything, text
    assert len(everything) == len(set(everything))                          # nichts doppelt im selben Menübaum


def test_video_button_dropdown_and_shared_timelapse_action(qt_app, tmp_path, monkeypatch):
    window = window_for(tmp_path, monkeypatch)
    assert window.video_button.text() == "● Video"
    dropdown = labels(window.video_more_button.menu())
    assert dropdown[:3] == ["Videoaufnahme starten / stoppen …", "Zeitraffer starten …", "Android-Video starten"]
    video_sub = next(a.menu() for a in more_menu(window).actions() if a.text() == "Video")
    assert window.timelapse_action in video_sub.actions() and window.timelapse_action in window.video_more_button.menu().actions()
    started = []
    monkeypatch.setattr(window, "toggle_timelapse", lambda: started.append(True))
    window.timelapse_action.trigger()
    assert started == [True]


def test_share_button_offers_phone_and_beautify(qt_app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QToolButton
    window = window_for(tmp_path, monkeypatch)
    bottom = [b.text() for b in window.findChildren(QToolButton) if b.objectName() == "roundAction"]
    assert bottom == ["Aufnehmen", "Bearbeiten", "Kopieren", "Exportieren", "Teilen", "Dokument", "Mehr", "Schließen"]
    share = next(b for b in window.findChildren(QToolButton) if b.text() == "Teilen")
    assert labels(share.menu())[:2] == ["Aufs Handy senden (QR-Code) …", "Schicker Screenshot (Hintergrund, Rahmen) …"]
    ident = window.workspace.add(Image.new("RGB", (40, 30), "red"), title="Teilen")
    window.refresh()
    window.select_id(ident)
    opened = []
    monkeypatch.setattr(window, "beautify_selected", lambda: opened.append("schick"))
    next(a for a in share.menu().actions() if a.text().startswith("Schicker")).trigger()
    assert opened == ["schick"]


def test_editor_tools_are_grouped(qt_app):
    from PySide6.QtWidgets import QFrame
    from screenkaffee.editor import EditorWindow
    editor = EditorWindow(Image.new("RGB", (80, 60)), "x")
    separators = [f for f in editor.findChildren(QFrame) if f.objectName() == "toolSeparator"]
    assert len(separators) == 5 and len(editor.palette_actions) == 20
    editor.close()
