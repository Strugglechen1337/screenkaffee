"""Übersetzung der Oberfläche: Deutsch (Quelltext) und Englisch.

Der deutsche Text im Code ist der Schlüssel. ``tr("Speichern")`` liefert auf Englisch
den Eintrag aus den Wörterbüchern in ``screenkaffee/i18n_en/`` und sonst den deutschen
Text unverändert. Platzhalter werden mit ``str.format`` gefüllt:

    tr("{count} Aufnahmen", count=len(rows))

Texte in Modul-Konstanten (z. B. Listen von Aktionen) mit ``N_()`` markieren und erst
bei der Anzeige mit ``tr()`` übersetzen, weil die Sprache beim Import noch nicht
feststehen muss.

Die Sprache kommt aus der Umgebungsvariable ``SCREENKAFFEE_LANG`` (``de``/``en``), sonst
aus ``settings.json`` (Schlüssel ``language``: ``auto``/``de``/``en``), sonst aus der
Systemsprache. Ein Wechsel wirkt nach dem Neustart.
"""
import json
import locale
import os
import sys

def N_(text):
    """Text nur markieren (für Konstanten); übersetzt wird später mit tr()."""
    return text


# Sprachnamen bleiben in der eigenen Sprache („Deutsch“, „English“).
LANGUAGES = [("auto", N_("Automatisch (Systemsprache)")), ("de", "Deutsch"), ("en", "English")]

_language = None
_catalog = None


def system_language():
    """'de' bei deutscher Systemsprache, sonst 'en'."""
    if sys.platform == "win32":
        try:
            import ctypes
            if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x07:
                return "de"
            return "en"
        except (AttributeError, OSError):
            pass
    for name in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(name, "")
        if value and value not in ("C", "POSIX") and not value.startswith("C."):
            return "de" if value.lower().startswith("de") else "en"
    try:
        code = locale.getlocale()[0] or ""
    except ValueError:
        code = ""
    return "de" if code.lower().startswith(("de", "german")) else "en"


def configured_language():
    """Gespeicherte Einstellung ('auto', 'de' oder 'en') aus settings.json."""
    try:
        from .workspace import portable_root
        data = json.loads((portable_root() / "settings.json").read_text(encoding="utf-8"))
        value = data.get("language", "auto")
        return value if value in ("auto", "de", "en") else "auto"
    except (OSError, ValueError, AttributeError):
        return "auto"


def resolve(setting):
    return system_language() if setting == "auto" else setting


def language():
    global _language
    if _language is None:
        forced = os.environ.get("SCREENKAFFEE_LANG", "").lower()
        _language = forced if forced in ("de", "en") else resolve(configured_language())
    return _language


def set_language(code):
    """Sprache für diesen Prozess festlegen ('de', 'en' oder 'auto'); vor allem für Tests."""
    global _language
    _language = resolve(code) if code == "auto" else code


def catalog():
    global _catalog
    if _catalog is None:
        from .i18n_en import CATALOG
        _catalog = CATALOG
    return _catalog


def tr(text, **values):
    """Text in der eingestellten Sprache; fehlt eine Übersetzung, bleibt der deutsche Text."""
    if language() == "en":
        text = catalog().get(text, text)
    return text.format(**values) if values else text
