import sys
import uuid
import pytest
from screenkaffee import autostart


def test_command_for_the_packaged_app_starts_minimised(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\screenkaffee\screenkaffee.exe")
    assert autostart.command() == r'"C:\Apps\screenkaffee\screenkaffee.exe" --minimized'


def test_command_for_a_source_checkout_uses_the_module(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    command = autostart.command()
    assert command.endswith('-m screenkaffee --minimized') and command.startswith('"')


@pytest.fixture
def private_run_key(monkeypatch):
    """A throw-away registry key under HKCU so tests never touch the real autostart list."""
    import winreg
    parent = r"Software\screenkaffee-test-" + uuid.uuid4().hex[:8]
    monkeypatch.setattr(autostart, "RUN_KEY", parent + r"\Run")
    yield
    for path in (parent + r"\Run", parent):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            pass


@pytest.mark.skipif(sys.platform != "win32", reason="Registry nur unter Windows")
def test_windows_autostart_roundtrip(private_run_key):
    assert not autostart.is_enabled()
    autostart.set_enabled(True)
    assert autostart.is_enabled() and autostart.registered_command() == autostart.command()
    autostart.set_enabled(False)
    assert not autostart.is_enabled()
    autostart.set_enabled(False)


@pytest.mark.skipif(sys.platform != "win32", reason="Registry nur unter Windows")
def test_refresh_repoints_a_stale_entry_but_never_enables(private_run_key):
    autostart.refresh()
    assert not autostart.is_enabled()
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, autostart.RUN_KEY) as key:
        winreg.SetValueEx(key, autostart.VALUE_NAME, 0, winreg.REG_SZ, r'"D:\alt\screenkaffee.exe" --minimized')
    autostart.refresh()
    assert autostart.registered_command() == autostart.command()


def test_linux_autostart_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert not autostart.is_enabled()
    autostart.set_enabled(True)
    entry = tmp_path / "autostart" / "screenkaffee.desktop"
    assert entry.is_file() and "Exec=" + autostart.command() in entry.read_text(encoding="utf-8")
    assert autostart.is_enabled() and autostart.registered_command() == autostart.command()
    autostart.set_enabled(False)
    assert not entry.exists() and not autostart.is_enabled()
