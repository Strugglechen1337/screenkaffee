import sys
from PySide6.QtCore import QAbstractNativeEventFilter, QTimer

MOD = {"ctrl": 2, "strg": 2, "alt": 1, "shift": 4, "umschalt": 4, "meta": 8, "win": 8}
NAMED = {"print": 0x2C, "druck": 0x2C, "pause": 0x13, "insert": 0x2D, "ins": 0x2D, "del": 0x2E, "delete": 0x2E,
         "home": 0x24, "end": 0x23, "pgup": 0x21, "pgdown": 0x22, "space": 0x20, "tab": 0x09, "return": 0x0D,
         "scrolllock": 0x91, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}
ACTIONS = [("region", "Bereich aufnehmen"), ("screen", "Bildschirm aufnehmen"), ("window", "Fenster aufnehmen"),
           ("previous", "Vorheriger Bereich"), ("all", "Alle Bildschirme"), ("scroll", "Scrollaufnahme"), ("color", "Farbpipette"),
           ("manual-scroll", "Manuelle Scrollserie starten"), ("scroll-frame", "Scrollserie: nächstes Bild"), ("scroll-finish", "Scrollserie: fertigstellen"),
           ("video", "Video starten / stoppen"), ("video-pause", "Video pausieren / fortsetzen"), ("timelapse", "Zeitraffer starten / stoppen"),
           ("steps", "Schritt-Anleitung starten / beenden"), ("draw", "Live-Zeichnen ein / aus"), ("measure", "Bildschirm ausmessen (Lineal)"),
           ("zoom-in", "Video: hineinzoomen"), ("zoom-out", "Video: herauszoomen"), ("zoom-reset", "Video: Zoom zurücksetzen")]
DEFAULT_HOTKEYS = {**{action: "" for action, _ in ACTIONS}, "region": "Print",
                   "zoom-in": "Ctrl+Alt+Up", "zoom-out": "Ctrl+Alt+Down", "zoom-reset": "Ctrl+Alt+Home"}
DEFAULT_HOTKEYS.update({"scroll-frame": "Ctrl+Alt+F7", "scroll-finish": "Ctrl+Alt+F8", "draw": "Ctrl+Alt+D"})


GERMAN = {"ctrl": "Strg", "strg": "Strg", "alt": "Alt", "shift": "Umschalt", "umschalt": "Umschalt", "meta": "Win", "win": "Win",
          "print": "Druck", "druck": "Druck", "up": "Pfeil oben", "down": "Pfeil unten", "left": "Pfeil links", "right": "Pfeil rechts",
          "home": "Pos1", "end": "Ende", "del": "Entf", "delete": "Entf", "insert": "Einfg", "ins": "Einfg",
          "pgup": "Bild auf", "pgdown": "Bild ab", "space": "Leertaste", "return": "Eingabe", "scrolllock": "Rollen"}


def display_hotkey(text):
    """'Ctrl+Alt+F8' -> 'Strg+Alt+F8' (German key names as on the keyboard)."""
    return "+".join(GERMAN.get(part.strip().lower(), part.strip().upper() if len(part.strip()) <= 3 else part.strip())
                    for part in text.split("+"))


def action_label(action):
    return dict(ACTIONS).get(action, action)


def parse_hotkey(text):
    """'Ctrl+Shift+Print' -> (modifier flags, virtual-key code); ValueError if unusable."""
    modifiers, key = 0, None
    for part in (p.strip().lower() for p in text.split("+")):
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
                raise ValueError("Unbekannte Taste: " + part)
        else:
            raise ValueError("Ungültiges Tastenkürzel: " + text)
    if key is None:
        raise ValueError("Es fehlt eine Taste.")
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
                self.failed[action] = f"{action_label(action)}: {display_hotkey(text)} ist in screenkaffee bereits einer anderen Aktion zugewiesen."
                continue
            used.add((modifiers, key))
            if key == 0x2C and modifiers == 0 and self.install_print_hook(action):
                continue
            if self.ctypes.windll.user32.RegisterHotKey(None, ident, 0x4000 | modifiers, key):
                self.ids[ident] = action
            else:
                self.failed[action] = f"{action_label(action)}: {display_hotkey(text)} ist bereits von Windows oder einem anderen Programm belegt."
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
