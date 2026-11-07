r"""Erkennung einer Installation per Setup (Windows, pro Benutzer) und Pflege ihres Uninstall-Eintrags.

Das Setup legt unter HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\<AppId>_is1 einen Eintrag an, dessen
`InstallLocation` auf den App-Ordner zeigt. Wir suchen diesen Eintrag über den Ordner – die AppId muss nicht bekannt
sein. Es wird nur HKCU gelesen und geschrieben, nie HKLM. Fehler führen nie zum Abbruch, sie werden nur protokolliert.
"""
import logging
import os
from pathlib import Path
import sys

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
MARKER = "installed-by-setup.json"      # Ersatz, falls ein Setup keinen Registry-Eintrag schreibt (optional)


def _same_folder(left, right):
    def norm(value):
        return os.path.normcase(os.path.normpath(str(value).strip().strip('"'))) if str(value).strip() else ""
    return bool(norm(left)) and norm(left) == norm(right)


def find_entry(root=None, registry=None):
    """Name des Uninstall-Schlüssels (z. B. '{...}_is1'), dessen InstallLocation `root` ist, sonst None."""
    if registry is None:
        if sys.platform != "win32":
            return None
        import winreg as registry
    from .paths import application_root
    root = Path(root or application_root())
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, UNINSTALL_KEY) as parent:
            index = 0
            while True:
                try:
                    name = registry.EnumKey(parent, index)
                except OSError:
                    return None
                index += 1
                if not name.endswith("_is1"):
                    continue
                try:
                    with registry.OpenKey(parent, name) as key:
                        location = registry.QueryValueEx(key, "InstallLocation")[0]
                except OSError:
                    continue
                if _same_folder(location, root):
                    return name
    except OSError:
        return None


def is_installed_by_setup(root=None, registry=None):
    """True, wenn dieser App-Ordner vom Setup stammt (Uninstall-Eintrag oder Markierungsdatei)."""
    from .paths import application_root
    root = Path(root or application_root())
    try:
        if find_entry(root, registry) is not None:
            return True
        return (root / MARKER).is_file()
    except Exception:
        logging.warning("Setup-Erkennung fehlgeschlagen", exc_info=True)
        return False


def update_display_version(version, root=None, registry=None):
    """Trägt `version` als DisplayVersion im Uninstall-Eintrag ein (nur wenn nötig). True, wenn geschrieben."""
    try:
        if registry is None:
            if sys.platform != "win32":
                return False
            import winreg as registry
        name = find_entry(root, registry)
        if name is None:
            return False
        with registry.OpenKey(registry.HKEY_CURRENT_USER, UNINSTALL_KEY + "\\" + name, 0,
                              registry.KEY_READ | registry.KEY_SET_VALUE) as key:
            try:
                if registry.QueryValueEx(key, "DisplayVersion")[0] == version:
                    return False
            except OSError:
                pass
            registry.SetValueEx(key, "DisplayVersion", 0, registry.REG_SZ, version)
        return True
    except Exception:
        logging.warning("DisplayVersion im Uninstall-Eintrag konnte nicht aktualisiert werden", exc_info=True)
        return False
