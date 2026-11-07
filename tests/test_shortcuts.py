import sys
from pathlib import Path

import pytest

from screenkaffee import installed, shortcuts, updater


# --- Reine Logik ---------------------------------------------------------------------------------------------------

def test_launch_target_for_package_and_source(tmp_path):
    (tmp_path / "screenkaffee.exe").write_text("x")
    target = shortcuts.launch_target(tmp_path, platform="win32")
    assert Path(target.program) == tmp_path / "screenkaffee.exe" and target.arguments == ""
    assert Path(target.workdir) == tmp_path and Path(target.icon) == tmp_path / "screenkaffee.exe"
    source = shortcuts.launch_target(tmp_path / "leer", platform="win32")
    assert source.arguments == "-m screenkaffee" and Path(source.program).name.startswith("python")
    (tmp_path / "screenkaffee").write_text("#!/bin/sh")
    linux = shortcuts.launch_target(tmp_path, platform="linux")
    assert Path(linux.program) == tmp_path / "screenkaffee"


def test_desktop_entry_content_and_quoting():
    target = shortcuts.Target("/opt/screen kaffee/screenkaffee", "", "/opt/screen kaffee", "/opt/screen kaffee/icon.png")
    lines = shortcuts.desktop_entry(target).splitlines()
    assert lines[0] == "[Desktop Entry]" and "Type=Application" in lines and "Name=screenkaffee" in lines
    assert 'Exec="/opt/screen kaffee/screenkaffee"' in lines and "Path=/opt/screen kaffee" in lines
    assert "Icon=/opt/screen kaffee/icon.png" in lines and "Terminal=false" in lines
    assert shortcuts.quote_exec("/opt/x/screenkaffee") == "/opt/x/screenkaffee"
    plain = shortcuts.desktop_entry(shortcuts.Target("/usr/bin/python3", "-m screenkaffee"))
    assert "-m screenkaffee" in plain and "Icon=" not in plain


def test_locations_files_use_platform_suffix(tmp_path, monkeypatch):
    places = shortcuts.Locations(tmp_path / "D", tmp_path / "M")
    monkeypatch.setattr(sys, "platform", "win32")
    assert [(k, p.name) for k, p in places.files()] == [("desktop", "screenkaffee.lnk"), ("menu", "screenkaffee.lnk")]
    monkeypatch.setattr(sys, "platform", "linux")
    assert [p.name for _, p in places.files()] == ["screenkaffee.desktop"] * 2
    assert [k for k, _ in shortcuts.Locations(None, tmp_path / "M").files()] == ["menu"]


def test_linux_create_check_and_remove_only_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    places = shortcuts.Locations(tmp_path / "Desktop", tmp_path / "apps", tmp_path / "icons")
    target = shortcuts.Target("/opt/sk/screenkaffee", "", "/opt/sk", "")
    assert not shortcuts.is_complete(places)
    written = []
    created = shortcuts.create(places, target, icon_writer=lambda folder: written.append(folder) or folder / "screenkaffee.png")
    assert len(created) == 2 and shortcuts.is_complete(places) and written == [tmp_path / "icons"] * 2
    text = (tmp_path / "apps" / "screenkaffee.desktop").read_text(encoding="utf-8")
    assert "Exec=/opt/sk/screenkaffee" in text.splitlines() and "Icon=" in text
    assert shortcuts.exists(places) == {"desktop": True, "menu": True}
    assert sorted(shortcuts.remove(places)) == sorted(created) and not shortcuts.exists(places)["menu"]
    assert shortcuts.remove(places) == []             # zweites Entfernen ist harmlos


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-Verknüpfungen")
def test_windows_link_is_written_in_tmp(tmp_path):
    exe = tmp_path / "screenkaffee.exe"
    exe.write_text("x")
    link = tmp_path / "Desktop" / "screenkaffee.lnk"
    link.parent.mkdir()
    shortcuts.write_link(link, shortcuts.Target(str(exe), "--minimized", str(tmp_path), str(exe)))
    data = link.read_bytes()
    assert link.is_file() and len(data) > 100
    assert str(exe).encode("utf-16-le") in data and "--minimized".encode("utf-16-le") in data


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-Verknüpfungen")
def test_windows_link_powershell_fallback_in_tmp(tmp_path):
    exe = tmp_path / "screenkaffee.exe"
    exe.write_text("x")
    link = tmp_path / "fallback.lnk"
    shortcuts._write_link_powershell(link, shortcuts.Target(str(exe), "", str(tmp_path), str(exe)))
    assert link.is_file() and str(exe).encode("utf-16-le") in link.read_bytes()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-Shell")
def test_windows_known_folders_exist():
    assert shortcuts.known_folder(shortcuts.FOLDERID_DESKTOP).is_dir()
    assert shortcuts.known_folder(shortcuts.FOLDERID_PROGRAMS).is_dir()


# --- Setup-Erkennung und Uninstall-Eintrag (gemockte Registry) ------------------------------------------------------

class FakeKey:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeRegistry:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ, KEY_SET_VALUE, REG_SZ = 1, 2, 1

    def __init__(self, entries):
        self.entries = {f"{installed.UNINSTALL_KEY}\\{name}": dict(values) for name, values in entries.items()}
        self.writes = []

    def OpenKey(self, parent, name, reserved=0, access=0):
        path = name if parent == "HKCU" else parent.path + "\\" + name
        if path != installed.UNINSTALL_KEY and path not in self.entries:
            raise FileNotFoundError(path)
        return FakeKey(path)

    def EnumKey(self, key, index):
        names = [path.rsplit("\\", 1)[1] for path in self.entries]
        if index >= len(names):
            raise OSError("keine weiteren")
        return names[index]

    def QueryValueEx(self, key, name):
        try:
            return self.entries[key.path][name], self.REG_SZ
        except KeyError:
            raise FileNotFoundError(name)

    def SetValueEx(self, key, name, reserved, kind, value):
        self.entries[key.path][name] = value
        self.writes.append((key.path, name, value))


def app_entries(location, version="1.2.0"):
    return {"{ABC}_is1": {"InstallLocation": location, "DisplayVersion": version},
            "Andere_is1": {"InstallLocation": "C:\\Fremd\\"}, "Ohne": {"InstallLocation": location}}


def test_setup_installation_is_found_by_install_location(tmp_path):
    root = tmp_path / "Programs" / "screenkaffee"
    registry = FakeRegistry(app_entries(str(root) + "\\"))              # Inno schreibt mit abschließendem Backslash
    assert installed.find_entry(root, registry) == "{ABC}_is1"
    assert installed.is_installed_by_setup(root, registry)
    assert not installed.is_installed_by_setup(tmp_path / "anderer-ordner", registry)


def test_marker_file_also_counts_as_setup(tmp_path):
    assert not installed.is_installed_by_setup(tmp_path, FakeRegistry({}))
    (tmp_path / installed.MARKER).write_text("{}", encoding="utf-8")
    assert installed.is_installed_by_setup(tmp_path, FakeRegistry({}))


def test_display_version_is_updated_only_for_own_folder(tmp_path):
    root = tmp_path / "screenkaffee"
    registry = FakeRegistry(app_entries(str(root)))
    assert installed.update_display_version("1.3.0", root, registry) is True
    assert registry.entries[f"{installed.UNINSTALL_KEY}\\{{ABC}}_is1"]["DisplayVersion"] == "1.3.0"
    assert installed.update_display_version("1.3.0", root, registry) is False       # schon aktuell: nichts schreiben
    assert len(registry.writes) == 1
    foreign = FakeRegistry(app_entries("C:\\Fremd\\"))
    assert installed.update_display_version("1.3.0", root, foreign) is False and foreign.writes == []


def test_display_version_errors_are_only_logged(tmp_path, caplog):
    class Broken(FakeRegistry):
        def SetValueEx(self, *args):
            raise PermissionError("verweigert")
    root = tmp_path / "screenkaffee"
    assert installed.update_display_version("9.9.9", root, Broken(app_entries(str(root)))) is False
    assert "DisplayVersion" in caplog.text


def test_registry_access_is_limited_to_current_user():
    source = Path(installed.__file__).read_text(encoding="utf-8")
    assert "HKEY_LOCAL_MACHINE" not in source


# --- Updater-Erkennung im Setup-Ordner --------------------------------------------------------------------------

@pytest.mark.parametrize("variant,files", [("windows-x64", ["_internal/lib.dll", "screenkaffee.exe"]),
                                           ("windows-arm64", ["runtime/pythonw.exe", "screenkaffee.exe"])])
def test_installed_variant_is_unchanged_in_setup_folder(tmp_path, monkeypatch, variant, files):
    monkeypatch.setattr(sys, "platform", "win32")
    root = tmp_path / "AppData" / "Local" / "Programs" / "screenkaffee"
    for name in files:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("x")
    assert updater.installed_variant(root) == variant
    assert installed.is_installed_by_setup(root, FakeRegistry(app_entries(str(root) + "\\")))
    assert updater.installed_variant(root) == variant


# --- Einrichtung ---------------------------------------------------------------------------------------------------

def window_for(tmp_path, monkeypatch):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    monkeypatch.setattr(MainWindow, "apply_hotkeys", lambda self: {})
    return MainWindow(Workspace(tmp_path))


def quiet_autostart(monkeypatch):
    from screenkaffee import autostart
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    monkeypatch.setattr(autostart, "set_enabled", lambda value: None)


def test_setup_offers_shortcuts_for_zip_and_creates_them(qt_app, tmp_path, monkeypatch):
    quiet_autostart(monkeypatch)
    monkeypatch.setattr(installed, "is_installed_by_setup", lambda *a, **k: False)
    window = window_for(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(shortcuts, "create", lambda places, **kw: calls.append(places) or [Path("a"), Path("b")])
    dialog = window.first_run_setup()
    assert dialog.shortcuts is not None and dialog.shortcuts.isChecked()
    assert dialog.shortcuts.text() == "Verknüpfungen auf dem Desktop und im Startmenü anlegen"
    dialog.accept()
    assert len(calls) == 1


def test_setup_hides_shortcuts_when_installed_by_setup(qt_app, tmp_path, monkeypatch):
    quiet_autostart(monkeypatch)
    monkeypatch.setattr(installed, "is_installed_by_setup", lambda *a, **k: True)
    window = window_for(tmp_path, monkeypatch)
    monkeypatch.setattr(shortcuts, "create", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nicht anlegen")))
    dialog = window.first_run_setup()
    assert dialog.shortcuts is None
    dialog.accept()


def test_setup_later_creates_no_shortcuts(qt_app, tmp_path, monkeypatch):
    quiet_autostart(monkeypatch)
    monkeypatch.setattr(installed, "is_installed_by_setup", lambda *a, **k: False)
    window = window_for(tmp_path, monkeypatch)
    monkeypatch.setattr(shortcuts, "create", lambda *a, **k: (_ for _ in ()).throw(AssertionError("nicht anlegen")))
    window.first_run_setup().reject()


def test_unchecking_removes_existing_shortcuts(qt_app, tmp_path, monkeypatch):
    quiet_autostart(monkeypatch)
    monkeypatch.setattr(installed, "is_installed_by_setup", lambda *a, **k: False)
    window = window_for(tmp_path, monkeypatch)
    window.create_shortcuts()
    dialog = window.first_run_setup()
    assert dialog.shortcuts.isChecked()                 # vorhandene Verknüpfungen: angehakt
    dialog.shortcuts.setChecked(False)
    dialog.accept()
    assert not any(shortcuts.exists(shortcuts.locations()).values())


def test_menu_entry_creates_shortcuts_in_tmp_locations(qt_app, tmp_path, monkeypatch):
    window = window_for(tmp_path, monkeypatch)
    places = shortcuts.locations()
    assert "shortcuts" in str(places.menu)               # von der conftest auf einen Temp-Ordner gelegt
    created = window.create_shortcuts()
    assert len(created) == 2 and all(path.is_file() and "shortcuts" in str(path) for path in created)
    assert "Verknüpfung" in window.statusBar().currentMessage()
    texts = []

    def walk(menu):
        for action in menu.actions():
            texts.append(action.text())
            if action.menu():
                walk(action.menu())
    walk(window.more_menu)
    assert "Verknüpfungen anlegen (Desktop, Startmenü) …" in texts


def test_startup_updates_display_version(qt_app, tmp_path, monkeypatch):
    from screenkaffee import __version__
    calls = []
    monkeypatch.setattr(installed, "update_display_version", lambda version, *a, **k: calls.append(version) or False)
    window = window_for(tmp_path, monkeypatch)
    window.startup_update()
    assert calls == [__version__]
