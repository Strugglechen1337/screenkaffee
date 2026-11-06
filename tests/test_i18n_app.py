"""Englische Oberfläche des Hauptfensters: Menüs, Dialoge, Tour, Tastennamen, Sprachfrage und Neustart."""
import sys

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox

from screenkaffee import i18n


@pytest.fixture
def english():
    i18n.set_language("en")
    yield
    i18n.set_language("de")


def window_for(tmp_path, monkeypatch):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    monkeypatch.setattr(MainWindow, "apply_hotkeys", lambda self: {})      # nie echte globale Tasten registrieren
    return MainWindow(Workspace(tmp_path))


def menu_texts(menu):
    texts = []
    for action in menu.actions():
        if action.isSeparator():
            continue
        texts.append(action.text())
        if action.menu() is not None:
            texts += menu_texts(action.menu())
    return texts


# --- Hauptfenster -----------------------------------------------------------------------------------------------

def test_main_window_menus_and_buttons_are_english(qt_app, tmp_path, monkeypatch, english):
    window = window_for(tmp_path, monkeypatch)
    assert window.windowTitle().endswith("Your screenshot studio")
    assert window.capture_button.text() == "+ Capture region"
    assert window.edit_button.text() == "Edit" and window.share_button.text() == "Share"
    assert window.more_button.text() == "More" and window.version_button.text().startswith("Version ")
    texts = menu_texts(window.more_menu)
    assert {"Capture", "Help and updates", "Find command… (Ctrl+K)", "Start time-lapse…", "Record step guide…",
            "Send to phone (QR code)…", "Beautify (background, frame)…"} <= set(texts)
    assert not [text for text in texts if any(ch in text for ch in "äöüÄÖÜß")], texts
    assert menu_texts(window.tray.contextMenu())[-1] == "Quit"
    assert window.search.placeholderText().startswith("Filter captures")
    assert window.count_label.text() == "0 captures"
    window.close()


def test_collections_show_english_names_but_keep_their_keys(qt_app, tmp_path, monkeypatch, english):
    from screenkaffee.workspace import Workspace
    from PySide6.QtCore import Qt
    from screenkaffee.app_widgets import COLLECTION_KIND
    workspace = Workspace(tmp_path)
    workspace.create_collection("Projekt")
    first = workspace.add(Image.new("RGB", (20, 20), "red"), "eins")
    second = workspace.add(Image.new("RGB", (20, 20), "blue"), "zwei", collection="Projekt")
    workspace.remove(first)
    from screenkaffee.app import MainWindow
    monkeypatch.setattr(MainWindow, "apply_hotkeys", lambda self: {})
    window = MainWindow(workspace)
    labels = [window.collection_list.item(i).text() for i in range(window.collection_list.count())]
    assert labels == ["All captures", "Projekt", "Trash (1)", "+ New…"]
    assert window.current_collection() == "Alle Aufnahmen" and window.list.count() == 1
    window.collection_chosen(window.collection_list.item(1))
    assert window.current_collection() == "Projekt" and window.list.count() == 1
    window.collection_chosen(window.collection_list.item(2))
    assert window.in_trash() and window.current_collection() == "" and window.list.count() == 1
    window.collection_chosen(window.collection_list.item(0))
    assert window.current_collection() == "Alle Aufnahmen"
    kinds = [window.collection_list.item(i).data(COLLECTION_KIND) for i in range(window.collection_list.count())]
    assert kinds == ["collection", "collection", "trash", "new"]
    assert window.collection_list.item(1).data(Qt.ItemDataRole.UserRole) == "Projekt"
    window.refresh_collections()                    # Auswahl überlebt den Neuaufbau
    assert window.current_collection() == "Alle Aufnahmen"
    assert workspace.get(second)["collection"] == "Projekt"
    window.close()


def test_context_menus_and_dialogs_build_in_english(qt_app, tmp_path, monkeypatch, english):
    window = window_for(tmp_path, monkeypatch)
    ident = window.workspace.add(Image.new("RGB", (40, 30), "red"), "Bild")
    window.refresh()
    window.select_id(ident)
    shown = []

    def close_popup():
        popup = QApplication.activePopupWidget()
        if isinstance(popup, QMenu):
            shown.append(menu_texts(popup))
            popup.close()
    from PySide6.QtCore import QTimer
    QTimer.singleShot(50, close_popup)                 # das Kontextmenü läuft modal mit eigener Ereignisschleife
    window.show_capture_context_menu(window.list.mapToGlobal(window.list.rect().center()))
    assert "Edit (Ctrl+E)" in shown[-1] and "Remove selection (Del)" in shown[-1]
    titles = []

    def fake_exec(dialog):
        titles.append(dialog.windowTitle())
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog, "exec", fake_exec)
    from PySide6.QtMultimedia import QMediaDevices
    from screenkaffee import audio
    monkeypatch.setattr(QMediaDevices, "videoInputs", staticmethod(lambda: []))      # keine echten Geräte abfragen
    monkeypatch.setattr(QMediaDevices, "audioInputs", staticmethod(lambda: []))
    monkeypatch.setattr(audio, "input_devices", lambda: [])
    window.preferences()
    window.configure_video()
    assert window.scroll_options() is None
    window.batch_export_selected()
    assert titles == ["Settings", "Video recording", "Scrolling capture", "Batch export: 1 image(s)"]
    timelapse = window.configure_timelapse()
    assert timelapse.windowTitle() == "Time-lapse"
    timelapse.reject()
    from screenkaffee import autostart
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    setup = window.first_run_setup()
    assert setup.windowTitle() == "Welcome to screenkaffee"
    setup.reject()
    notes = window.show_update_result({"notes": "- new"})
    assert notes.windowTitle().startswith("New in screenkaffee")
    notes.close()
    window.close()


def test_status_texts_with_placeholders_work_in_english(qt_app, tmp_path, monkeypatch, english):
    window = window_for(tmp_path, monkeypatch)
    window.settings.update(auto_edit=False, auto_copy=False)
    window.finish_capture(Image.new("RGB", (30, 20), "red"), {"backend": "test"})
    assert window.statusBar().currentMessage() == "30 × 20 px saved · test"
    window.show_preview()
    assert "30 × 20 pixels" in window.detail.text()
    window.show_version("v9.9.9")
    assert window.version_button.text().endswith("update 9.9.9 available")
    window.report_hotkey_problems({"region": "Region: Print Screen is already in use"})
    assert window.statusBar().currentMessage().startswith("Keyboard shortcut not available – ")
    assert window.speech_status_text()
    window.close()


def test_tour_palette_progress_and_crash_dialog_in_english(qt_app, tmp_path, monkeypatch, english):
    from screenkaffee import tour
    from screenkaffee.crash import crash_report, show_crash_dialog
    from screenkaffee.palette import CommandPalette
    from screenkaffee.progress import Progress, ProgressWindow, describe
    steps = tour.steps({"hotkeys": {"region": "Print", "draw": "Ctrl+Alt+D"}})
    texts = {key: text for key, _, _, text in steps}
    assert "<b>Print Screen</b>" in texts["capture"] and "<b>Ctrl+Alt+D</b>" in texts["livedraw"]
    assert "Ctrl+click" in texts["compare"]
    window = window_for(tmp_path, monkeypatch)
    window.show()
    overlay = window.start_tour()
    assert overlay.counter.text() == f"Step 1 of {len(steps)}" and overlay.next_button.text() == "Next"
    overlay.end(False)
    palette = CommandPalette(window.command_entries(), window)
    assert palette.windowTitle() == "Find command" and palette.listing.count() > 10
    palette.close()
    progress = Progress([("download", 0.85, "Herunterladen und prüfen"), ("extract", 0.15, "Entpacken")])
    progress_window = ProgressWindow("Update", "Downloading", progress, window)
    assert progress_window.step.text() == "Step 1 of 2: Downloading and verifying"
    assert progress_window.cancel_button.text() == "Cancel"
    assert describe(45e6, 110e6, 10) == "45 of 110 MB · 4.5 MB/s · about 14 s left"
    progress_window.finish("done")
    progress_window.close()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        report = crash_report(*sys.exc_info())
    assert "Error:" in report
    dialog = show_crash_dialog(report)
    assert dialog.copy_button.text() == "Copy report"
    dialog.close()
    window.close()


# --- Tastennamen -----------------------------------------------------------------------------------------------

def test_hotkeys_are_shown_in_the_ui_language_and_parsed_in_both(english):
    from screenkaffee.hotkeys import action_label, display_hotkey, hotkey_for_settings, parse_hotkey
    assert display_hotkey("Ctrl+Shift+Print") == "Ctrl+Shift+Print Screen"
    assert display_hotkey("Strg+Umschalt+Druck") == "Ctrl+Shift+Print Screen"
    assert display_hotkey("Ctrl+Alt+Up") == "Ctrl+Alt+Up" and display_hotkey("pgup") == "Page Up"
    assert display_hotkey("Ctrl+Alt+Up", "de") == "Strg+Alt+Pfeil oben"
    assert action_label("scroll-finish") == "Scroll series: finish"
    for text in ("Ctrl+S", "Strg+S", "ctrl+s", "Control+S"):
        assert parse_hotkey(text) == (2, ord("S"))
    for text in ("Print", "Druck", "Print Screen", "PrtSc"):
        assert parse_hotkey(text) == (0, 0x2C)
    assert parse_hotkey("Ctrl+Bild auf") == parse_hotkey("Ctrl+Page Up") == (2, 0x21)
    assert parse_hotkey("Umschalt+Pfeil oben") == parse_hotkey("Shift+Up") == (4, 0x26)
    assert parse_hotkey("Entf") == parse_hotkey("Del") and parse_hotkey("Pos1") == parse_hotkey("Home")
    with pytest.raises(ValueError, match="Unknown key"):
        parse_hotkey("Ctrl+Blub")
    # gespeichert wird im bisherigen Format: unverändert bleibt der alte Text, neue Schreibweisen werden kanonisch
    assert hotkey_for_settings("Ctrl+Alt+Up", "Ctrl+Alt+Up") == "Ctrl+Alt+Up"
    assert hotkey_for_settings("Print Screen", "Print") == "Print"
    assert hotkey_for_settings("Ctrl+Page Up", "Print") == "Ctrl+PgUp"
    assert hotkey_for_settings("Strg+Umschalt+S", "Print") == "Strg+Umschalt+S"
    assert hotkey_for_settings("  ", "Print") == ""


def test_german_hotkey_display_keeps_the_old_storage_format(qt_app, tmp_path, monkeypatch):
    from screenkaffee.hotkeys import hotkey_for_settings
    assert hotkey_for_settings("Strg+Alt+Pfeil oben", "Ctrl+Alt+Up") == "Ctrl+Alt+Up"
    assert hotkey_for_settings("Strg+Bild ab", "") == "Strg+PgDown"
    window = window_for(tmp_path, monkeypatch)
    window.settings["hotkeys"]["region"] = "Print"
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    from screenkaffee import autostart
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    window.preferences()                                 # nichts geändert -> Speicherform bleibt erhalten
    assert window.settings["hotkeys"]["region"] == "Print"
    assert window.settings["hotkeys"]["zoom-in"] == "Ctrl+Alt+Up"
    window.close()


def test_default_locale_follows_the_language():
    from PySide6.QtCore import QLocale
    from screenkaffee.__main__ import set_default_locale
    try:
        assert set_default_locale("en").toString(0.5, "f", 1) == "0.5"
        assert QLocale().language() == QLocale.Language.English
        assert set_default_locale("de").toString(0.5, "f", 1) == "0,5"
        assert QLocale().language() == QLocale.Language.German
    finally:
        set_default_locale("de")


# --- Sprachfrage beim ersten Start und Neustart ------------------------------------------------------------------

def test_language_dialog_saves_choice_and_restarts_only_on_change(qt_app, tmp_path, monkeypatch):
    window = window_for(tmp_path, monkeypatch)
    restarts, continued = [], []
    monkeypatch.setattr(window, "restart_app", lambda: restarts.append(True) or True)
    dialog = window.ask_language(then=lambda: continued.append(True))
    assert dialog.windowTitle() == "Sprache wählen / Choose language"
    dialog.german.setChecked(True)
    dialog.accept()
    assert window.settings["language"] == "de" and window.settings["language_asked"] is True
    assert restarts == [] and continued == [True]
    import json
    assert json.loads(window.settings_path.read_text(encoding="utf-8"))["language_asked"] is True
    dialog = window.ask_language(then=lambda: continued.append(True), notes={"notes": "- neu"})
    dialog.english.setChecked(True)
    dialog.accept()
    assert window.settings["language"] == "en" and restarts == [True] and continued == [True]
    assert window.settings["pending_update_notes"] == {"notes": "- neu"}      # Notizen überleben den Neustart
    window.close()


def test_language_question_is_asked_once_before_setup_and_tour(qt_app, tmp_path, monkeypatch):
    window = window_for(tmp_path, monkeypatch)
    asked, setup, tours = [], [], []
    monkeypatch.setenv("QT_QPA_PLATFORM", "windows")          # Start wie beim echten Programm
    monkeypatch.setattr(window, "ask_language", lambda then=None, notes=None: asked.append(then))
    monkeypatch.setattr(window, "first_run_setup", lambda: setup.append(True))
    monkeypatch.setattr(window, "schedule_tour", lambda: tours.append(True))
    window.settings["update_mode"] = "off"
    window.startup_update()
    assert len(asked) == 1 and setup == [] and tours == []         # Einrichtung/Tour erst nach der Sprachwahl
    asked[0]()
    assert setup == [True] and tours == [True]
    window.settings.update(language_asked=True, tour_done=True, setup_done=True)
    window.startup_update()
    assert len(asked) == 1                                           # bestehende Nutzer nur einmal fragen
    window.close()


def test_settings_offer_restart_for_a_new_language(qt_app, tmp_path, monkeypatch):
    window = window_for(tmp_path, monkeypatch)
    restarts = []
    monkeypatch.setattr(window, "restart_app", lambda: restarts.append(True) or True)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    assert window.offer_language_restart("de") is False and restarts == []
    assert window.offer_language_restart("en") is True and restarts == [True]
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    assert window.offer_language_restart("en") is False and restarts == [True]
    window.close()


def test_restart_quits_cleanly_and_starts_the_same_program(qt_app, tmp_path, monkeypatch):
    from screenkaffee import updater
    window = window_for(tmp_path, monkeypatch)
    monkeypatch.setattr(updater, "installed_variant", lambda root=None: None)
    monkeypatch.setenv("SCREENKAFFEE_DATA", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["screenkaffee", "--capture", "region", "--minimized"])
    command, folder, env = window.restart_command()
    assert command == [sys.executable, "-m", "screenkaffee", "--data-dir", str(tmp_path)]
    assert env["SCREENKAFFEE_DATA"] == str(tmp_path)
    root = tmp_path / "app"
    monkeypatch.setattr(updater, "installed_variant", lambda root=None: "windows-x64")
    monkeypatch.setattr(updater, "application_root", lambda: root)
    command, folder, env = window.restart_command()
    assert command[0] == str(root / "screenkaffee.exe") and folder == str(root)
    launched, quits = [], []
    monkeypatch.setattr(window, "launch_restart", lambda: launched.append(True))
    monkeypatch.setattr(QApplication, "quit", lambda *args: quits.append(True))
    window.editors.append(type("Busy", (), {"close": lambda self: False})())
    assert window.restart_app() is False and launched == [] and not window.restart_requested
    window.editors.clear()
    assert window.restart_app() is True and launched == [True] and quits == [True]
