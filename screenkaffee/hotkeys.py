import sys
from PySide6.QtCore import QAbstractNativeEventFilter, QTimer

from .i18n import N_, language, tr

# Gespeichert werden Tastenkürzel wie bisher als Text ("Print", "Ctrl+Alt+F8", aber auch "Strg+Umschalt+S").
# MOD und NAMED sind die Namen, die auch ältere Versionen verstehen (wichtig für „Vorige Version wiederherstellen“).
MOD = {"ctrl": 2, "strg": 2, "alt": 1, "shift": 4, "umschalt": 4, "meta": 8, "win": 8}
NAMED = {"print": 0x2C, "druck": 0x2C, "pause": 0x13, "insert": 0x2D, "ins": 0x2D, "del": 0x2E, "delete": 0x2E,
         "home": 0x24, "end": 0x23, "pgup": 0x21, "pgdown": 0x22, "space": 0x20, "tab": 0x09, "return": 0x0D,
         "scrolllock": 0x91, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}
# Weitere Schreibweisen (die angezeigten Namen beider Sprachen) -> Name aus NAMED.
ALIASES = {"druck": "print", "delete": "del", "ins": "insert",
           "print screen": "print", "printscreen": "print", "prtsc": "print", "prtscn": "print",
           "page up": "pgup", "pageup": "pgup", "page down": "pgdown", "pagedown": "pgdown", "pgdn": "pgdown",
           "enter": "return", "scroll lock": "scrolllock", "control": "ctrl",
           "entf": "del", "einfg": "insert", "pos1": "home", "ende": "end", "bild auf": "pgup", "bildauf": "pgup",
           "bild ab": "pgdown", "bildab": "pgdown", "leertaste": "space", "eingabe": "return", "rollen": "scrolllock",
           "pfeil oben": "up", "pfeil unten": "down", "pfeil links": "left", "pfeil rechts": "right"}
# Speicherform für Tasten, die nur über ALIASES erkannt werden (z. B. "Bild auf" -> "PgUp").
CANONICAL = {"print": "Print", "pause": "Pause", "insert": "Insert", "del": "Del", "home": "Home", "end": "End",
             "pgup": "PgUp", "pgdown": "PgDown", "space": "Space", "tab": "Tab", "return": "Return",
             "scrolllock": "ScrollLock", "left": "Left", "up": "Up", "right": "Right", "down": "Down", "ctrl": "Ctrl"}
ACTIONS = [("region", N_("Bereich aufnehmen")), ("screen", N_("Bildschirm aufnehmen")), ("window", N_("Fenster aufnehmen")),
           ("previous", N_("Vorheriger Bereich")), ("all", N_("Alle Bildschirme")), ("scroll", N_("Scrollaufnahme")),
           ("color", N_("Farbpipette")), ("manual-scroll", N_("Manuelle Scrollserie starten")),
           ("scroll-frame", N_("Scrollserie: nächstes Bild")), ("scroll-finish", N_("Scrollserie: fertigstellen")),
           ("video", N_("Video starten / stoppen")), ("video-pause", N_("Video pausieren / fortsetzen")),
           ("timelapse", N_("Zeitraffer starten / stoppen")), ("steps", N_("Schritt-Anleitung starten / beenden")),
           ("draw", N_("Live-Zeichnen ein / aus")), ("measure", N_("Bildschirm ausmessen (Lineal)")),
           ("zoom-in", N_("Video: hineinzoomen")), ("zoom-out", N_("Video: herauszoomen")), ("zoom-reset", N_("Video: Zoom zurücksetzen"))]
DEFAULT_HOTKEYS = {**{action: "" for action, _ in ACTIONS}, "region": "Print",
                   "zoom-in": "Ctrl+Alt+Up", "zoom-out": "Ctrl+Alt+Down", "zoom-reset": "Ctrl+Alt+Home"}
DEFAULT_HOTKEYS.update({"scroll-frame": "Ctrl+Alt+F7", "scroll-finish": "Ctrl+Alt+F8", "draw": "Ctrl+Alt+D"})


# Anzeigenamen je Sprache (Schlüssel: Name aus MOD/NAMED nach ALIASES).
GERMAN = {"ctrl": "Strg", "strg": "Strg", "alt": "Alt", "shift": "Umschalt", "umschalt": "Umschalt", "meta": "Win", "win": "Win",
          "print": "Druck", "druck": "Druck", "up": "Pfeil oben", "down": "Pfeil unten", "left": "Pfeil links", "right": "Pfeil rechts",
          "home": "Pos1", "end": "Ende", "del": "Entf", "delete": "Entf", "insert": "Einfg", "ins": "Einfg",
          "pgup": "Bild auf", "pgdown": "Bild ab", "space": "Leertaste", "return": "Eingabe", "scrolllock": "Rollen"}  # i18n: ignore (Anzeigenamen)
ENGLISH = {"ctrl": "Ctrl", "strg": "Ctrl", "alt": "Alt", "shift": "Shift", "umschalt": "Shift", "meta": "Win", "win": "Win",
           "print": "Print Screen", "pause": "Pause", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
           "home": "Home", "end": "End", "del": "Del", "insert": "Insert", "pgup": "Page Up", "pgdown": "Page Down",
           "space": "Space", "tab": "Tab", "return": "Enter", "scrolllock": "Scroll Lock"}


def _key_name(part):
    low = part.strip().lower()
    return ALIASES.get(low, low)


def display_hotkey(text, lang=None):
    """'Ctrl+Alt+F8' -> 'Strg+Alt+F8' (deutsch) bzw. 'Ctrl+Alt+F8' (englisch): Tastennamen wie auf der Tastatur."""
    names = ENGLISH if (lang or language()) == "en" else GERMAN
    return "+".join(names.get(_key_name(part), part.strip().upper() if len(part.strip()) <= 3 else part.strip())
                    for part in text.split("+"))


def storage_hotkey(text):
    """Eingabe in Speicherform: bekannte Namen bleiben wie getippt, nur neue Schreibweisen ('Bild auf',
    'Print Screen') werden zu Namen, die alle Versionen verstehen ('PgUp', 'Print')."""
    parts = []
    for part in (p.strip() for p in text.strip().split("+")):
        low = part.lower()
        if low in MOD or low in NAMED or low not in ALIASES:
            parts.append(part)
        else:
            parts.append(CANONICAL.get(ALIASES[low], part))
    return "+".join(parts)


def hotkey_for_settings(entered, stored=""):
    """Wert für settings['hotkeys'] aus einem Eingabefeld, das display_hotkey(stored) zeigte: unverändert bleibt
    der gespeicherte Text erhalten, sonst wird die Eingabe (deutsch oder englisch) übernommen."""
    entered = entered.strip()
    if stored and entered == display_hotkey(stored):
        return stored
    return storage_hotkey(entered) if entered else ""


def action_label(action):
    return tr(dict(ACTIONS).get(action, action))


def parse_hotkey(text):
    """'Ctrl+Shift+Print' oder 'Strg+Umschalt+Druck' -> (modifier flags, virtual-key code); ValueError if unusable."""
    modifiers, key = 0, None
    for part in (_key_name(p) for p in text.split("+")):
        if part in MOD:
            modifiers |= MOD[part]
        elif key is None and part:
            if part in NAMED:
                key = NAMED[part]
            elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
                key = 0x70 + int(part[1:]) - 1
            elif len(part) == 1 and part.isalnum() and part.isascii():
                key = ord(part.upper())
            else:
                raise ValueError(tr("Unbekannte Taste: {key}", key=part))
        else:
            raise ValueError(tr("Ungültiges Tastenkürzel: {hotkey}", hotkey=text))
    if key is None:
        raise ValueError(tr("Es fehlt eine Taste."))
    return modifiers, key


class WindowsHotkeys(QAbstractNativeEventFilter):
    """Global hotkeys per action. Registration can fail if another program owns the key."""
    BASE_ID = 0x5052

    def __init__(self, callbacks):
        super().__init__()
        import ctypes
        self.ctypes = ctypes
        self.callbacks = callbacks      # action -> callable
        self.ids = {}                   # hotkey id -> action
        self.failed = {}                # action -> reason
        self.registered = False
        self.hook = None
        self.hook_callback = None
        self.print_action = None
        self.print_down = False
        self.pending = []
        self.timer = QTimer()
        self.timer.timeout.connect(self.deliver_pending)

    def deliver_pending(self):
        pending, self.pending = self.pending, []
        for action in pending:
            self.callbacks[action]()

    def print_event(self, message, modifiers=0):
        if not self.print_action or modifiers:
            return False
        if message in (0x100, 0x104):
            if not self.print_down:
                self.pending.append(self.print_action)
            self.print_down = True
        elif message in (0x101, 0x105):
            # Some keyboards emit PrintScreen on release only.
            if not self.print_down:
                self.pending.append(self.print_action)
            self.print_down = False
        return True

    def install_print_hook(self, action):
        c = self.ctypes
        from ctypes import wintypes
        user = c.windll.user32
        callback_type = c.WINFUNCTYPE(c.c_ssize_t, c.c_int, c.c_size_t, c.c_ssize_t)
        user.SetWindowsHookExW.argtypes = [c.c_int, callback_type, wintypes.HINSTANCE, wintypes.DWORD]
        user.SetWindowsHookExW.restype = wintypes.HANDLE
        user.CallNextHookEx.argtypes = [wintypes.HANDLE, c.c_int, c.c_size_t, c.c_ssize_t]
        user.CallNextHookEx.restype = c.c_ssize_t
        user.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        def callback(code, message, address):
            if code >= 0 and c.cast(address, c.POINTER(wintypes.DWORD))[0] == 0x2C:
                modifiers = any(user.GetAsyncKeyState(k) & 0x8000 for k in (0x10, 0x11, 0x12, 0x5B, 0x5C))
                if self.print_event(message, modifiers):
                    return 1
            return user.CallNextHookEx(None, code, message, address)
        self.hook_callback = callback_type(callback)
        self.print_action = action
        self.hook = user.SetWindowsHookExW(13, self.hook_callback, None, 0)
        if not self.hook:
            self.print_action = None
            return False
        self.timer.start(20)
        return True

    def bind(self, bindings):
        self.close()
        self.failed = {}
        used = set()
        for index, (action, text) in enumerate(bindings.items()):
            if not text or action not in self.callbacks:
                continue
            try:
                modifiers, key = parse_hotkey(text)
            except ValueError as exc:
                self.failed[action] = f"{action_label(action)}: {exc}"
                continue
            ident = self.BASE_ID + index
            if (modifiers, key) in used:
                self.failed[action] = tr("{action}: {key} ist in screenkaffee bereits einer anderen Aktion zugewiesen.",
                                         action=action_label(action), key=display_hotkey(text))
                continue
            used.add((modifiers, key))
            if key == 0x2C and modifiers == 0 and self.install_print_hook(action):
                continue
            if self.ctypes.windll.user32.RegisterHotKey(None, ident, 0x4000 | modifiers, key):
                self.ids[ident] = action
            else:
                self.failed[action] = tr("{action}: {key} ist bereits von Windows oder einem anderen Programm belegt.",
                                         action=action_label(action), key=display_hotkey(text))
        self.registered = bool(self.ids or self.hook)
        return self.failed

    def nativeEventFilter(self, event_type, message):
        from ctypes import wintypes
        msg = self.ctypes.cast(int(message), self.ctypes.POINTER(wintypes.MSG)).contents
        if msg.message == 0x0312 and msg.wParam in self.ids:
            self.callbacks[self.ids[msg.wParam]]()
            return True, 0
        return False, 0

    def close(self):
        from shiboken6 import isValid
        if isValid(self.timer):  # beim Programmende kann Qt den Timer schon gelöscht haben
            self.timer.stop()
        if self.hook:
            self.ctypes.windll.user32.UnhookWindowsHookEx(self.hook)
        self.hook = None
        self.print_action = None
        self.print_down = False
        self.pending.clear()
        for ident in list(self.ids):
            self.ctypes.windll.user32.UnregisterHotKey(None, ident)
        self.ids = {}
        self.registered = False
