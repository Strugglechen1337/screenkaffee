"""Verknüpfungen für den aktuellen Benutzer: Desktop und Startmenü (Windows) bzw. Anwendungsmenü (Linux).

Ohne Adminrechte, nichts systemweit. Die Logik (Ziel, Pfade, Inhalt der .desktop-Datei) ist von den echten Ordnern
getrennt: `Locations` lässt sich in Tests auf einen Temp-Ordner richten. Echte Verknüpfungen entstehen nur, wenn
der Aufrufer die echten Ordner (`locations()`) übergibt.
"""
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import subprocess
import sys

from .i18n import tr
from .paths import application_root

NAME = "screenkaffee"
COMMENT = "Screenshots und Bildschirmvideos"  # i18n: ignore (Beschreibung in der Verknüpfungsdatei)
ICON_NAME = "screenkaffee"


@dataclass
class Target:
    """Was eine Verknüpfung startet."""
    program: str                 # ausführbare Datei
    arguments: str = ""          # Kommandozeile hinter dem Programm
    workdir: str = ""            # Arbeitsverzeichnis (App-Ordner)
    icon: str = ""               # Datei, aus der das Symbol stammt (Windows: exe/ico, Linux: png/svg)


@dataclass
class Locations:
    """Ordner, in die Verknüpfungen gelegt werden. None = nicht anlegen."""
    desktop: Path = None
    menu: Path = None            # Windows: Startmenü\\Programme; Linux: ~/.local/share/applications
    icons: Path = None           # Linux: Ordner für ein selbst erzeugtes Symbol

    def files(self):
        """Alle Verknüpfungsdateien in Anlegereihenfolge: [(Art, Pfad)]."""
        suffix = ".lnk" if sys.platform == "win32" else ".desktop"
        items = []
        if self.desktop is not None:
            items.append(("desktop", Path(self.desktop) / (NAME + suffix)))
        if self.menu is not None:
            items.append(("menu", Path(self.menu) / (NAME + suffix)))
        return items


# --- Ziel --------------------------------------------------------------------------------------------------------

def launch_target(root=None, platform=None):
    """Startet diese Kopie der App (Paket) bzw. den Quellcode über den Python-Interpreter."""
    platform = platform or sys.platform
    root = Path(root or application_root())
    if platform == "win32":
        launcher = root / "screenkaffee.exe"
        if getattr(sys, "frozen", False):
            launcher = Path(sys.executable)
        if launcher.is_file() or getattr(sys, "frozen", False):
            return Target(str(launcher), "", str(root), str(launcher))
        executable = Path(sys.executable)
        windowless = executable.with_name("pythonw.exe")
        if windowless.exists():
            executable = windowless
        return Target(str(executable), "-m screenkaffee", str(root), "")
    launcher = root / "screenkaffee"
    if launcher.is_file():
        return Target(str(launcher), "", str(root), _linux_icon(root))
    return Target(sys.executable, "-m screenkaffee", str(root), _linux_icon(root))


def _linux_icon(root):
    for name in ("screenkaffee.svg", "screenkaffee.png"):
        if (Path(root) / name).is_file():
            return str(Path(root) / name)
    return ""


def desktop_entry(target, icon=None):
    """Inhalt der .desktop-Datei für das Anwendungsmenü (Freedesktop-Format)."""
    command = quote_exec(target.program) + (" " + target.arguments if target.arguments else "")
    lines = ["[Desktop Entry]", "Type=Application", f"Name={NAME}", f"Comment={COMMENT}", f"Exec={command}"]
    if target.workdir:
        lines.append(f"Path={target.workdir}")
    icon = icon or target.icon
    if icon:
        lines.append(f"Icon={icon}")
    lines += ["Terminal=false", "Categories=Utility;Graphics;"]
    return "\n".join(lines) + "\n"


def quote_exec(path):
    """Exec-Wert nach der Desktop-Spezifikation: Leerzeichen und Sonderzeichen erfordern Anführungszeichen."""
    path = str(path)
    if any(ch in path for ch in ' \t\n"\'\\><~|&;$*?#()`'):
        escaped = path.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
        return f'"{escaped}"'
    return path


# --- Ordner ------------------------------------------------------------------------------------------------------

def known_folder(guid):
    """Ordner eines Benutzers über die Shell-API (folgt umgeleiteten Ordnern, z. B. Desktop in OneDrive)."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]
    import uuid
    value = uuid.UUID(guid)
    ident = GUID(value.time_low, value.time_mid, value.time_hi_version, (ctypes.c_ubyte * 8)(*value.bytes[8:]))
    result = ctypes.c_wchar_p()
    shell32 = ctypes.windll.shell32
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                                             ctypes.POINTER(ctypes.c_wchar_p)]
    if shell32.SHGetKnownFolderPath(ctypes.byref(ident), 0, None, ctypes.byref(result)) != 0:
        raise OSError(tr("Ordner nicht gefunden: {name}", name=guid))
    try:
        return Path(result.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(result)


FOLDERID_DESKTOP = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"
FOLDERID_PROGRAMS = "A77F5D77-2E2B-44C3-A6A2-ABA601054A51"       # Startmenü\Programme des Benutzers


def locations(desktop=True):
    """Die echten Ordner des angemeldeten Benutzers."""
    if sys.platform == "win32":
        found = Locations(menu=known_folder(FOLDERID_PROGRAMS))
        if desktop:
            found.desktop = known_folder(FOLDERID_DESKTOP)
        return found
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    found = Locations(menu=data / "applications", icons=data / "icons" / "hicolor" / "256x256" / "apps")
    if desktop:
        found.desktop = _linux_desktop_dir()
    return found


def _linux_desktop_dir():
    configured = os.environ.get("XDG_DESKTOP_DIR")
    if configured:
        return Path(configured)
    desktop = Path.home() / "Desktop"
    return desktop if desktop.is_dir() else None


# --- Anlegen / Entfernen / Prüfen --------------------------------------------------------------------------------

def exists(places):
    """{Art: bool} – welche Verknüpfungen es schon gibt."""
    return {kind: path.is_file() for kind, path in places.files()}


def is_complete(places):
    state = exists(places)
    return bool(state) and all(state.values())


def create(places, target=None, icon_writer=None):
    """Legt die Verknüpfungen an (vorhandene werden aktualisiert). Gibt die angelegten Pfade zurück.

    `icon_writer(ordner)` darf unter Linux ein Symbol erzeugen und dessen Pfad liefern, wenn das Paket keines mitbringt."""
    target = target or launch_target()
    created = []
    for kind, path in places.files():
        path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            write_link(path, target)
        else:
            icon = target.icon
            if not icon and icon_writer is not None and places.icons is not None:
                try:
                    icon = str(icon_writer(Path(places.icons)) or "")
                except Exception:
                    logging.warning("Symbol für die Verknüpfung konnte nicht erzeugt werden", exc_info=True)
            path.write_text(desktop_entry(target, icon), encoding="utf-8")
            try:
                path.chmod(0o755)
            except OSError:
                pass
        created.append(path)
    return created


def remove(places):
    """Entfernt die Verknüpfungen; gibt die gelöschten Pfade zurück."""
    removed = []
    for _, path in places.files():
        if path.is_file():
            path.unlink()
            removed.append(path)
    return removed


def linux_icon_writer(folder):
    """Rendert das App-Symbol als PNG (das Paket bringt keine Symboldatei mit)."""
    from .app_widgets import app_icon
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (ICON_NAME + ".png")
    if not app_icon().pixmap(256, 256).save(str(target), "PNG"):
        raise OSError(tr("Symbol konnte nicht gespeichert werden"))
    return target


# --- Windows: .lnk schreiben -------------------------------------------------------------------------------------

def write_link(path, target):
    """Schreibt eine .lnk-Datei. Erst über IShellLinkW (ctypes), sonst über WScript.Shell in PowerShell."""
    try:
        _write_link_com(Path(path), target)
        return
    except Exception:
        logging.warning("Verknüpfung über IShellLink fehlgeschlagen, versuche PowerShell", exc_info=True)
    _write_link_powershell(Path(path), target)


def _write_link_com(path, target):
    import ctypes
    from ctypes import wintypes
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    def guid(text):
        value = uuid.UUID(text)
        return GUID(value.time_low, value.time_mid, value.time_hi_version, (ctypes.c_ubyte * 8)(*value.bytes[8:]))

    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)         # S_FALSE (schon initialisiert, z. B. durch Qt) ist in Ordnung
    ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(GUID),
                                       ctypes.POINTER(ctypes.c_void_p)]
    link = ctypes.c_void_p()
    clsid, iid = guid("00021401-0000-0000-C000-000000000046"), guid("000214F9-0000-0000-C000-000000000046")
    result = ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(link))
    if result != 0 or not link.value:
        raise OSError(f"CoCreateInstance: {result:#x}")

    def method(pointer, index, *argtypes):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(table[index])

    persist = ctypes.c_void_p()
    try:
        method(link, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
            link, ctypes.byref(guid("0000010B-0000-0000-C000-000000000046")), ctypes.byref(persist))
        try:
            method(link, 20, wintypes.LPCWSTR)(link, target.program)                      # SetPath
            method(link, 11, wintypes.LPCWSTR)(link, target.arguments)                    # SetArguments
            method(link, 9, wintypes.LPCWSTR)(link, target.workdir or str(Path(target.program).parent))
            method(link, 7, wintypes.LPCWSTR)(link, COMMENT)                              # SetDescription
            icon = target.icon or target.program
            method(link, 17, wintypes.LPCWSTR, ctypes.c_int)(link, icon, 0)               # SetIconLocation
            method(persist, 6, wintypes.LPCWSTR, wintypes.BOOL)(persist, str(path), True)  # IPersistFile::Save
        finally:
            method(persist, 2)(persist)                                                   # Release
    finally:
        method(link, 2)(link)


def _write_link_powershell(path, target):
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:SK_LINK);"
              "$s.TargetPath = $env:SK_PROGRAM; $s.Arguments = $env:SK_ARGS; $s.WorkingDirectory = $env:SK_DIR;"
              "$s.Description = $env:SK_TEXT; $s.IconLocation = $env:SK_ICON + ',0'; $s.Save()")
    env = dict(os.environ, SK_LINK=str(path), SK_PROGRAM=target.program, SK_ARGS=target.arguments,
               SK_DIR=target.workdir or str(Path(target.program).parent), SK_TEXT=COMMENT,
               SK_ICON=target.icon or target.program)
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env, check=True,
                   capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
