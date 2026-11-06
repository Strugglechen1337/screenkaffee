"""Start with the user's login, minimised to the tray. Per-user only: no admin rights, nothing system-wide."""
import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "screenkaffee"
DESKTOP_NAME = "screenkaffee.desktop"


def command():
    """Command line that starts this copy of the app hidden in the tray."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    if os.environ.get("SCREENKAFFEE_APPDIR"):
        launcher = Path(os.environ["SCREENKAFFEE_APPDIR"]).resolve() / ("screenkaffee.exe" if sys.platform == "win32" else "screenkaffee")
        if launcher.is_file():
            return f'"{launcher}" --minimized'
    executable = Path(sys.executable)
    windowless = executable.with_name("pythonw.exe")
    if sys.platform == "win32" and windowless.exists():
        executable = windowless
    return f'"{executable}" -m screenkaffee --minimized'


def linux_entry_path():
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / DESKTOP_NAME


def registered_command():
    """The command currently registered for autostart, or None."""
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                return winreg.QueryValueEx(key, VALUE_NAME)[0]
        except FileNotFoundError:
            return None
    path = linux_entry_path()
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Exec="):
            return line[5:]
    return None


def is_enabled():
    return registered_command() is not None


def set_enabled(enabled):
    if sys.platform == "win32":
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return
    path = linux_entry_path()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[Desktop Entry]\nType=Application\nName=screenkaffee\n"
                        f"Exec={command()}\nX-GNOME-Autostart-enabled=true\n", encoding="utf-8")
    else:
        path.unlink(missing_ok=True)


def refresh():
    """Keep a registered entry pointing at this copy (the portable folder may have been moved)."""
    current = registered_command()
    if current is not None and current != command():
        set_enabled(True)
