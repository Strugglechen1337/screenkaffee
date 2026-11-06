"""Recording helpers: fixed-size recording areas, a click effect and pressed keys drawn on screen while recording."""
import sys
from .i18n import N_, tr
import time
from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import QWidget

RECORD_SIZES = [("1920 × 1080", (1920, 1080)), ("1280 × 720", (1280, 720)), ("854 × 480", (854, 480)), ("640 × 360", (640, 360))]


def preset_region(screen_bounds, size):
    """Region [x, y, w, h] of the given size centred on a screen (native pixels); shrinks to fit the screen."""
    left, top, width, height = screen_bounds
    box_width, box_height = min(size[0], width), min(size[1], height)
    box_width, box_height = box_width // 2 * 2, box_height // 2 * 2          # even sizes for H.264
    return [left + (width - box_width) // 2, top + (height - box_height) // 2, box_width, box_height]


def zoom_frame_rect(region, factor, fx, fy):
    """Part of the region shown at the given zoom, centred on (fx, fy) in 0..1 and kept inside the region.
    Same maths as the zoom filter applied when the video is saved. Returns [x, y, w, h]."""
    x, y, width, height = region
    visible_w, visible_h = width / factor, height / factor
    left = max(0.0, min(width - visible_w, fx * width - visible_w / 2))
    top = max(0.0, min(height - visible_h, fy * height - visible_h / 2))
    return [x + left, y + top, visible_w, visible_h]


class ZoomFrame(QWidget):
    """Click-through green frame that marks the area currently shown by the recording zoom."""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool |
                            Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    def show_area(self, rect, scale=1.0):
        """rect in physical pixels; scale = device pixel ratio of the screen."""
        x, y, width, height = (round(v / scale) for v in rect)
        self.setGeometry(x, y, max(4, width), max(4, height))
        self.show()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#22c55e"), 4))
        painter.drawRect(self.rect().adjusted(2, 2, -2, -2))


class LiveZoomPreview(QWidget):
    """Click-through live magnified preview window shown during video zoom."""

    def __init__(self, grabber=None, size=(240, 150)):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool |
                            Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.resize(*size)
        self.grabber = grabber
        self.area = [0, 0, size[0], size[1]]
        self.factor = 1.0
        self.scale = 1.0
        self.frame_pixmap = None

    def show_area(self, rect, factor=2.0, scale=1.0):
        self.area = list(rect)
        self.factor = float(factor)
        self.scale = float(scale or 1.0)
        self.refresh_frame()
        self.show()
        self.update()

    def refresh_frame(self):
        from PySide6.QtGui import QGuiApplication, QPixmap
        x, y, width, height = (round(v / self.scale) for v in self.area)
        width, height = max(2, width), max(2, height)
        if self.grabber is not None:
            pix = self.grabber(x, y, width, height)
        else:
            screen = QGuiApplication.primaryScreen()
            pix = screen.grabWindow(0, x, y, width, height) if screen is not None else QPixmap()
        if pix is not None and not pix.isNull():
            self.frame_pixmap = pix.scaled(self.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        else:
            self.frame_pixmap = None

    def paintEvent(self, event):
        from PySide6.QtGui import QFont
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#181818"))
        if self.frame_pixmap is not None:
            painter.drawPixmap(0, 0, self.frame_pixmap)
        painter.setPen(QPen(QColor("#22c55e"), 3))
        painter.drawRect(self.rect().adjusted(1, 1, -2, -2))
        painter.fillRect(4, 4, 110, 22, QColor(0, 0, 0, 180))
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.drawText(10, 19, tr("Zoom {factor}×", factor=f"{self.factor:g}"))


def windows_button_state():
    """(left, right) mouse buttons currently down; (False, False) where the platform offers no cheap way to ask."""
    if sys.platform != "win32":
        return False, False
    import ctypes
    state = ctypes.windll.user32.GetAsyncKeyState
    return bool(state(0x01) & 0x8000), bool(state(0x02) & 0x8000)


class ClickEffect(QWidget):
    """Click-through, always-on-top ring that appears where the mouse is pressed and fades out."""
    DURATION = .45

    def __init__(self, button_state=windows_button_state, position=QCursor.pos, clock=time.monotonic):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool |
                            Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.resize(120, 120)
        self.button_state, self.position, self.clock = button_state, position, clock
        self.previous = (False, False)
        self.started = None
        self.center = QPointF()
        self.color = QColor("#ffcc00")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)

    def start(self):
        self.timer.start(15)

    def stop(self):
        self.timer.stop()
        self.hide()

    def progress(self):
        return None if self.started is None else min(1.0, (self.clock() - self.started) / self.DURATION)

    def tick(self):
        pressed = self.button_state()
        if any(now and not before for now, before in zip(pressed, self.previous)):
            point = self.position()
            self.center = QPointF(point)
            self.started = self.clock()
            self.color = QColor("#ffcc00") if pressed[0] else QColor("#ff5a36")
            self.move(point.x() - self.width() // 2, point.y() - self.height() // 2)
            self.show()
        self.previous = tuple(pressed)
        progress = self.progress()
        if progress is None:
            return
        if progress >= 1.0:
            self.started = None
            self.hide()
        else:
            self.update()

    def paintEvent(self, event):
        progress = self.progress()
        if progress is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self.color)
        color.setAlpha(round(230 * (1 - progress)))
        painter.setPen(QPen(color, 5))
        radius = 14 + 40 * progress
        painter.drawEllipse(QPointF(self.width() / 2, self.height() / 2), radius, radius)


# --- Tastendrücke im Video einblenden ------------------------------------------------------------------------------

MODIFIERS = {0xA2: N_("Strg"), 0xA3: N_("Strg"), 0xA4: N_("Alt"), 0xA5: N_("Alt"), 0x5B: N_("Win"), 0x5C: N_("Win"), 0xA0: N_("Umschalt"), 0xA1: N_("Umschalt")}
MODIFIER_ORDER = (N_("Strg"), N_("Alt"), N_("Win"), N_("Umschalt"))
SPECIAL = {0x08: N_("Rück"), 0x09: N_("Tab"), 0x0D: N_("Eingabe"), 0x1B: N_("Esc"), 0x20: N_("Leertaste"), 0x21: N_("Bild ↑"), 0x22: N_("Bild ↓"),
           0x23: N_("Ende"), 0x24: N_("Pos1"), 0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓", 0x2C: N_("Druck"), 0x2D: N_("Einfg"), 0x2E: N_("Entf")}
KEYSTROKE_MODES = [("combos", N_("Nur Tastenkombinationen und Sondertasten")), ("all", N_("Alle Tasten (auch Getipptes)"))]
# Tasten, die nie einzeln gezeigt werden: Maus, generische Umschalt/Strg/Alt (die L/R-Varianten zählen), Feststell-Tasten.
IGNORED = {0x01, 0x02, 0x04, 0x05, 0x06, 0x10, 0x11, 0x12, 0x14, 0x90, 0x91, 0x15, 0x19}


def windows_char(vk):
    """Zeichen auf der Taste nach aktuellem Tastaturlayout (z. B. 'Ü', '#'), sonst ''."""
    if sys.platform != "win32":
        return ""
    import ctypes
    value = ctypes.windll.user32.MapVirtualKeyW(vk, 2) & 0xFFFF
    return chr(value).upper() if value >= 0x20 else ""


def key_name(vk, char_for=windows_char):
    if vk in SPECIAL:
        return SPECIAL[vk]
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x60 <= vk <= 0x69:
        return f"Num {vk - 0x60}"
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    return char_for(vk) or tr("Taste {vk}", vk=vk)


def is_typing(name):
    """Tasten, die beim normalen Schreiben gedrückt werden (Buchstaben, Ziffern, Satzzeichen, Leertaste, Rück)."""
    return len(name) == 1 or name.startswith("Num ") or name in ("Leertaste", "Rück")  # i18n: ignore – Stabile Taste für die Erkennungslogik; Anzeige wird übersetzt.


class KeyTracker:
    """Erkennt neu gedrückte Tasten und macht daraus Anzeigetexte wie 'Strg + Umschalt + S'.

    Im Modus 'combos' (Standard) erscheinen nur Kombinationen mit Strg/Alt/Win sowie Sondertasten wie Eingabe, Esc
    und F1 – normales Tippen (auch mit Umschalt) bleibt unsichtbar, damit z. B. Passwörter nicht im Video landen."""
    TYPE_PAUSE = 1.2          # so lange darf beim Tippen pausiert werden, ohne dass ein neues Feld beginnt

    def __init__(self, mode="combos", char_for=windows_char):
        self.mode, self.char_for = mode, char_for
        self.down = set()
        self.typed = ""
        self.last_typed = None

    def feed(self, pressed, now):
        """pressed: Menge der gerade gedrückten virtuellen Tastencodes. Liefert den neuen Anzeigetext oder None."""
        pressed = {vk for vk in pressed if vk not in IGNORED}
        new = sorted(pressed - self.down)
        self.down = pressed
        modifiers = {MODIFIERS[vk] for vk in pressed if vk in MODIFIERS}
        text = None
        for vk in new:
            if vk in MODIFIERS:
                continue
            name = key_name(vk, self.char_for)
            command = modifiers - {"Umschalt"}
            if command or not is_typing(name):
                if not command and self.mode == "combos" and name == "Tab":
                    continue                       # Tab beim Ausfüllen von Formularen nicht einblenden
                self.typed = ""
                text = " + ".join([tr(m) for m in MODIFIER_ORDER if m in modifiers] + [tr(name)])
            elif self.mode == "all":
                if self.last_typed is None or now - self.last_typed > self.TYPE_PAUSE:
                    self.typed = ""
                self.last_typed = now
                if name == "Rück":  # i18n: ignore – Stabile Taste für die Erkennungslogik; Anzeige wird übersetzt.
                    self.typed = self.typed[:-1]
                else:
                    self.typed = (self.typed + (" " if name == "Leertaste" else name.lower() if "Umschalt" not in modifiers else name))[-40:]
                text = self.typed or None
        return text


def windows_pressed_keys():
    if sys.platform != "win32":
        return set()
    import ctypes
    state = ctypes.windll.user32.GetAsyncKeyState
    return {vk for vk in range(0x08, 0xFF) if vk not in IGNORED and state(vk) & 0x8000}


class KeystrokeOverlay(QWidget):
    """Click-through Einblendung der gedrückten Tasten, unten mittig im Aufnahmebereich; blendet nach kurzer Zeit aus."""
    SHOW = 1.6
    FADE = .4

    def __init__(self, area, mode="combos", pressed=windows_pressed_keys, clock=time.monotonic, char_for=windows_char):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool |
                            Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.area = area                      # QRect in logischen Koordinaten
        self.tracker = KeyTracker(mode, char_for)
        self.pressed, self.clock = pressed, clock
        self.text = ""
        self.shown_at = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)

    def start(self):
        self.timer.start(30)

    def stop(self):
        self.timer.stop()
        self.hide()

    def opacity(self):
        if self.shown_at is None:
            return 0.0
        age = self.clock() - self.shown_at
        if age <= self.SHOW:
            return 1.0
        return max(0.0, 1 - (age - self.SHOW) / self.FADE)

    def tick(self):
        text = self.tracker.feed(self.pressed(), self.clock())
        if text:
            self.show_text(text)
        elif self.shown_at is not None and self.opacity() <= 0:
            self.shown_at = None
            self.hide()
        elif self.shown_at is not None:
            self.update()

    def show_text(self, text):
        from PySide6.QtGui import QFont, QFontMetrics
        self.text = text
        self.shown_at = self.clock()
        font = QFont(self.font())
        font.setPointSizeF(max(14.0, min(30.0, self.area.height() / 30)))
        font.setBold(True)
        self.setFont(font)
        metrics = QFontMetrics(font)
        width, height = metrics.horizontalAdvance(text) + 48, metrics.height() + 24
        x = self.area.x() + (self.area.width() - width) // 2
        y = self.area.y() + self.area.height() - height - max(24, self.area.height() // 12)
        self.setGeometry(x, y, width, height)
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event):
        alpha = self.opacity()
        if alpha <= 0 or not self.text:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(alpha)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 20, 20, 215))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 12, 12)
        painter.setPen(QColor("white"))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text)
