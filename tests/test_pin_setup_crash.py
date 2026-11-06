"""Anheften, Einrichtung beim ersten Start, Fehlerbericht und vorige Version wiederherstellen."""
import json
from pathlib import Path
import sys

from PIL import Image

from screenkaffee import updater


def window_for(tmp_path, monkeypatch):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    monkeypatch.setattr(MainWindow, "apply_hotkeys", lambda self: {})
    return MainWindow(Workspace(tmp_path))


# --- Anheften ------------------------------------------------------------------------------------------------------

def test_pinned_image_stays_on_top_and_zooms(qt_app):
    from PySide6.QtCore import Qt
    from screenkaffee.pin import PinnedImage, ZOOM_MAX
    window = PinnedImage(Image.new("RGB", (120, 80), "#336699"), "Rechnung")
    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert window.windowTitle() == "Angeheftet: Rechnung"
    assert (window.width(), window.height()) == window.logical_size()
    window.apply_zoom(2.0)
    assert window.width() == round(120 / window.dpr * 2)
    window.apply_zoom(99)
    assert window.zoom == ZOOM_MAX
    window.copy()
    assert qt_app.clipboard().image().width() == 120
    labels = [a.text() for a in window.build_menu().actions() if not a.isSeparator()]
    assert labels[0].startswith("In Zwischenablage kopieren") and "Schließen (Esc)" in labels
    window.set_opacity(0.5)
    assert abs(window.windowOpacity() - 0.5) < 0.01
    window.close()


def test_pin_from_workspace_and_editor(qt_app, tmp_path, monkeypatch):
    from screenkaffee import pin
    window = window_for(tmp_path, monkeypatch)
    ident = window.workspace.add(Image.new("RGB", (40, 30), "red"), title="Vergleich")
    window.refresh()
    window.select_id(ident)
    pinned = window.pin_selected()
    assert pinned in pin.OPEN and pinned.windowTitle() == "Angeheftet: Vergleich" and pinned.isVisible()
    edited = []
    monkeypatch.setattr(window, "open_editor", edited.append)
    pinned.on_edit(pinned.image)
    assert edited == [ident]
    pinned.close()
    from screenkaffee.editor import EditorWindow
    editor = EditorWindow(Image.new("RGB", (50, 20), "white"), "Bug")
    from_editor = editor.pin_current()
    assert from_editor.image.size == (50, 20)
    editor.close()
    assert from_editor.isVisible()          # bleibt nach dem Schließen des Editors offen
    from_editor.close()


# --- Fehlerbericht -------------------------------------------------------------------------------------------------

def test_crash_report_has_version_and_no_home_path(qt_app):
    from screenkaffee import __version__
    from screenkaffee.crash import crash_report, show_crash_dialog
    try:
        raise ValueError("Testfehler in " + str(Path.home() / "Bilder" / "geheim.png"))
    except ValueError:
        report = crash_report(*sys.exc_info())
    assert report.startswith(f"screenkaffee {__version__}")
    assert "ValueError: Testfehler" in report and "Python:" in report and "Qt/PySide6:" in report
    assert str(Path.home()) not in report and "~" in report
    dialog = show_crash_dialog(report, Path("log.txt"))
    dialog.copy_button.click()
    assert qt_app.clipboard().text() == report and dialog.copy_button.text() == "Kopiert ✓"
    dialog.close()


# --- Einrichtung ---------------------------------------------------------------------------------------------------

def test_first_run_setup_applies_choices(qt_app, tmp_path, monkeypatch):
    from screenkaffee import autostart
    window = window_for(tmp_path, monkeypatch)
    changes = []
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    monkeypatch.setattr(autostart, "set_enabled", changes.append)
    assert not window.settings.get("setup_done")
    dialog = window.first_run_setup()
    assert dialog.windowTitle() == "Willkommen bei screenkaffee"
    if dialog.hotkey is not None:
        dialog.hotkey.setCurrentText("Strg+Umschalt+S")
    else:
        assert "--capture region" in dialog.binding.text()
    dialog.autostart.setChecked(True)
    dialog.update_mode.setCurrentIndex(dialog.update_mode.findData("ask"))
    dialog.accept()
    assert changes == [True]
    assert window.settings["update_mode"] == "ask" and window.settings["setup_done"] is True
    assert not hasattr(dialog, "token")                 # kein Token mehr nötig (öffentliches Repository)
    if dialog.hotkey is not None:
        assert window.settings["hotkeys"]["region"] == "Strg+Umschalt+S"
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["setup_done"] is True


def test_setup_later_does_not_change_settings_but_is_not_asked_again(qt_app, tmp_path, monkeypatch):
    from screenkaffee import autostart
    window = window_for(tmp_path, monkeypatch)
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    monkeypatch.setattr(autostart, "set_enabled", lambda value: (_ for _ in ()).throw(AssertionError("nicht ändern")))
    dialog = window.first_run_setup()
    dialog.autostart.setChecked(True)
    dialog.reject()
    assert window.settings["setup_done"] is True and window.settings.get("update_mode", "off") == "off"


# --- Vorige Version ------------------------------------------------------------------------------------------------

def x64_layout(folder, marker):
    (folder / "_internal").mkdir(parents=True)
    (folder / "_internal" / "lib.dll").write_text(marker)
    (folder / "screenkaffee.exe").write_text(marker)


def test_previous_version_is_kept_and_can_be_restored(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    root = tmp_path / "app"
    x64_layout(root, "neu")
    staging = root / "_update"
    x64_layout(staging / "old", "alt")
    (staging / "done.json").write_text(json.dumps({"tag": "v0.8.0"}), encoding="utf-8")
    (staging / "previous.json").write_text(json.dumps({"version": "0.7.0"}), encoding="utf-8")
    assert updater.finish_previous(root) == {"tag": "v0.8.0"}
    assert (root / "_previous" / "screenkaffee.exe").read_text() == "alt" and not staging.exists()
    info = updater.previous_version(root)
    assert info == {"version": "0.7.0", "package": root / "_previous"}
    package = updater.prepare_restore(root)
    assert package == root / "_previous"
    notes = json.loads((staging / "release.json").read_text(encoding="utf-8"))
    assert notes["tag"] == "v0.7.0" and "wiederhergestellt" in notes["notes"]
    updater.apply(package, root)                           # das macht der Helfer aus _previous heraus
    assert (root / "screenkaffee.exe").read_text() == "alt"
    assert (root / "_previous" / "screenkaffee.exe").read_text() == "alt"     # Quelle bleibt unverändert
    updater.finish_previous(root)                          # nächster Start: die ersetzte Version wird die vorige
    assert (root / "_previous" / "screenkaffee.exe").read_text() == "neu"


def test_previous_version_number_falls_back_to_the_package_source(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    root = tmp_path / "app"
    x64_layout(root, "neu")
    x64_layout(root / "_previous", "alt")
    (root / "_previous" / "source" / "screenkaffee").mkdir(parents=True)
    (root / "_previous" / "source" / "screenkaffee" / "__init__.py").write_text('__version__ = "0.7.0"\n')
    (root / "_previous.json").write_text('{"version": ""}')         # so legt es ein Update von 0.7.0 aus an
    assert updater.previous_version(root)["version"] == "0.7.0"


def test_previous_version_is_dropped_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    root = tmp_path / "app"
    x64_layout(root, "neu")
    x64_layout(root / "_update" / "old", "alt")
    (root / "_update" / "done.json").write_text("{}", encoding="utf-8")
    updater.finish_previous(root, keep_previous=False)
    assert updater.previous_version(root) is None
    import pytest
    with pytest.raises(updater.UpdateError, match="keine vorige Version"):
        updater.prepare_restore(root)


def test_restore_from_menu_switches_auto_updates_off(qt_app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    window = window_for(tmp_path / "daten", monkeypatch)
    window.settings["update_mode"] = "auto"
    monkeypatch.setattr(updater, "previous_version", lambda root=None, variant=None: {"version": "0.7.0", "package": tmp_path})
    monkeypatch.setattr(updater, "prepare_restore", lambda root=None: tmp_path / "_previous")
    monkeypatch.setattr(updater, "installed_variant", lambda root=None: "windows-x64")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    restarts = []
    monkeypatch.setattr(window, "restart_for_update", lambda: restarts.append(window.pending_update))
    window.restore_previous_version()
    assert window.settings["update_mode"] == "off"
    assert restarts == [(tmp_path / "_previous", "windows-x64", "v0.7.0")]
