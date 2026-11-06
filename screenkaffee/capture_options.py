"""Settings that shape every new capture: title pattern, default export format, clipboard-only mode and
automatic post-processing (size, border, shadow, watermark, printing)."""
import logging
from datetime import datetime
import re
import sys
from .i18n import tr

DEFAULTS = {
    "title_pattern": "Aufnahme %d.%m.%Y %H:%M:%S",   # strftime codes plus {n} for a running number  # i18n: ignore – Gespeicherter Schlüssel oder Dateinamensmuster bleibt sprachunabhängig.
    "export_format": ".png",
    "clipboard_only": False,
    "auto_scale_percent": 100,
    "auto_border": 0,
    "auto_shadow": 0,
    "auto_watermark": "",
    "auto_trim": False,                  # einfarbige/durchsichtige Ränder nach der Aufnahme abschneiden
    "auto_print": False,
    "follow_mouse_screen": False,
    "capture_context": True,
    "confirm_delete": True,
    "print_margin_mm": 0,
    "audio_noise_gate_db": 0,
    "audio_timeshift_ms": 0,
    "audio_loopback_balance": 100,       # percent of the microphone level
    "animation_fps": 15,
    "animation_loops": 0,
}
EXPORT_FORMATS = (".png", ".jpg", ".jxl", ".gif", ".bmp", ".tiff", ".webp")
URL_RE = re.compile(r"https?://[^\s<>\"')]+|(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?:/[^\s<>\"')]*)?")


LOG = logging.getLogger(__name__)


def extract_url(text):
    """Extract a web URL or domain from a window title or context string, or return ''."""
    if not text:
        return ""
    match = URL_RE.search(str(text))
    if not match:
        return ""
    found = match.group(0).rstrip(".,;:-")
    return found if "://" in found else "https://" + found


def collect_capture_context(window_title="", application="", url=""):
    """Build capture context metadata (window title, application, URL) when available."""
    if not window_title and sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if hwnd:
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    window_title = buf.value.strip()
        except Exception:
            LOG.debug("Fenstertitel nicht lesbar", exc_info=True)
    if not application and window_title and " - " in window_title:
        application = window_title.rsplit(" - ", 1)[-1].strip()
    resolved_url = url or extract_url(window_title)
    result = {}
    if window_title:
        result["window_title"] = window_title
    if application:
        result["application"] = application
    if resolved_url:
        result["url"] = resolved_url
    return result


def title_for(pattern, number=1, now=None, context=None):
    """Capture title from a pattern. Invalid patterns fall back to the default so a capture is never lost."""
    now = now or datetime.now()
    context = context or {}
    app_name = context.get("application") or context.get("process_name") or context.get("app", "")
    win_name = context.get("window_title") or context.get("window", "")
    try:
        title = (now.strftime(pattern)
                 .replace("{n}", str(number))
                 .replace("{window}", str(win_name))
                 .replace("{app}", str(app_name))
                 .replace("{url}", str(context.get("url", "")))
                 .strip())
    except (ValueError, TypeError):
        title = ""
    return title[:200] or now.strftime(DEFAULTS["title_pattern"])


def pick_screen(screens, cursor, selected, follow_mouse=False):
    """Name of the screen to capture. screens: [(name, (x, y, w, h))]; cursor: (x, y) in the same coordinates.
    With follow_mouse the screen under the cursor wins (the chosen one is the fallback)."""
    if follow_mouse:
        for name, (x, y, width, height) in screens:
            if x <= cursor[0] < x + width and y <= cursor[1] < y + height:
                return name
    return selected


def validate(values):
    """Return {key: message} for settings that cannot be used."""
    problems = {}
    try:
        datetime.now().strftime(str(values.get("title_pattern", "")))
    except ValueError:
        problems["title_pattern"] = tr("Ungültiges Titelmuster.")
    if not str(values.get("title_pattern", "")).strip():
        problems["title_pattern"] = tr("Das Titelmuster darf nicht leer sein.")
    if values.get("export_format", ".png") not in EXPORT_FORMATS:
        problems["export_format"] = tr("Unbekanntes Standardformat.")
    checks = {"auto_scale_percent": (5, 400), "auto_border": (0, 100), "auto_shadow": (0, 200),
              "print_margin_mm": (0, 50), "animation_fps": (1, 60), "animation_loops": (0, 1000),
              "audio_noise_gate_db": (-90, 0), "audio_timeshift_ms": (-2000, 2000), "audio_loopback_balance": (10, 100)}
    for key, (low, high) in checks.items():
        value = values.get(key, DEFAULTS[key])
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            problems[key] = tr("Wert muss zwischen {low} und {high} liegen.", low=low, high=high)
    return problems


def effects_for(settings):
    """ImageEffects options for the automatic post-processing, or None when everything is off."""
    options = {}
    if settings.get("auto_scale_percent", 100) != 100:
        options["scale_percent"] = settings["auto_scale_percent"]
    if settings.get("auto_border", 0):
        options["border_width"] = settings["auto_border"]
    if settings.get("auto_shadow", 0):
        options["shadow_radius"] = settings["auto_shadow"]
    if str(settings.get("auto_watermark", "")).strip():
        options["watermark"] = settings["auto_watermark"].strip()
    return options or None


def post_process(image, settings):
    if settings.get("auto_trim"):
        from .effects import trim_bounds
        box = trim_bounds(image)
        if box is not None:
            image = image.crop(box)
    options = effects_for(settings)
    if not options:
        return image
    from .effects import apply_effects
    return apply_effects(image, options)
