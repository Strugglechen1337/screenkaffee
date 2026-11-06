"""Schritt-Anleitung aufnehmen: bei jedem Mausklick ein Bildschirmfoto mit markierter Klickstelle und Nummer.

Windows: Maustasten werden wie beim Klick-Effekt per ``GetAsyncKeyState`` abgefragt (nur gelesen, nichts
abgefangen). Unter Linux/Wayland gibt es keinen erlaubten Weg, Klicks in anderen Programmen zu sehen.
Klicks auf Fenster von screenkaffee selbst (z. B. die kleine Steuerleiste) zählen nicht als Schritt.
"""
import sys
import time
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

MAX_STEPS = 200
MIN_GAP = .35            # Sekunden zwischen zwei Schritten (Doppelklick = ein Schritt)


def supported():
    return sys.platform == "win32"


def mark_click(image, x, y, number, scale=1.0, color="#e5332a"):
    """Kopie des Bildes mit Ring um die Klickstelle und Nummernplakette daneben."""
    result = image.convert("RGB").copy()
    draw = ImageDraw.Draw(result, "RGBA")
    radius = max(10, round(22 * scale))
    width = max(3, round(4 * scale))
    rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=rgb + (55,), outline=rgb + (255,), width=width)
    badge = max(14, round(17 * scale))
    bx = x + radius + badge if x + radius + 2 * badge + 4 < result.width else x - radius - badge
    by = y - radius - badge // 2 if y - radius - badge - 4 > 0 else y + radius + badge // 2
    draw.ellipse((bx - badge, by - badge, bx + badge, by + badge), fill=rgb + (255,), outline=(255, 255, 255, 255), width=max(2, width // 2))
    try:
        font = ImageFont.load_default(size=round(badge * 1.2))
    except TypeError:                                   # sehr altes Pillow
        font = ImageFont.load_default()
    draw.text((bx, by), str(number), fill="white", font=font, anchor="mm")
    return result


def collection_name(now=None):
    return time.strftime("Anleitung %d.%m.%Y %H:%M", time.localtime(now))


@dataclass
class Step:
    image: Image.Image
    position: tuple
    window: str = ""


@dataclass
class StepRecorder:
    """Reine Logik: ``feed`` mit dem Tastenzustand; liefert True, wenn jetzt ein Schritt fällig ist."""
    min_gap: float = MIN_GAP
    previous: bool = False
    last: float = -1e9
    steps: list = field(default_factory=list)

    def feed(self, pressed, now):
        due = pressed and not self.previous and now - self.last >= self.min_gap and len(self.steps) < MAX_STEPS
        self.previous = pressed
        if due:
            self.last = now
        return due

    def add(self, image, x, y, scale=1.0, window=""):
        self.steps.append(Step(mark_click(image, x, y, len(self.steps) + 1, scale), (x, y), window))
        return self.steps[-1]


def foreground_title():
    """Titel des Fensters im Vordergrund (Windows), für die Schrittbeschreibung."""
    if sys.platform != "win32":
        return ""
    import ctypes
    user32 = ctypes.windll.user32
    handle = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(handle)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, length + 1)
    return buffer.value.strip()


def describe(step, number):
    where = f" in „{step.window}“" if step.window else ""
    return f"Schritt {number}: An der markierten Stelle klicken{where}."


class StepSession:
    """Qt-Teil: fragt die linke Maustaste ab, fotografiert den Bildschirm unter dem Mauszeiger, zeigt eine Steuerleiste."""

    def __init__(self, on_finish, on_cancel=None, button_state=None, grab=None, own_window=None, clock=time.monotonic):
        from PySide6.QtCore import QTimer
        from .videoextras import windows_button_state
        self.recorder = StepRecorder()
        self.on_finish, self.on_cancel = on_finish, on_cancel
        self.button_state = button_state or (lambda: windows_button_state()[0])
        self.grab = grab or grab_under_cursor
        self.own_window = own_window or click_on_own_window
        self.clock = clock
        self.timer = QTimer()
        self.timer.setInterval(30)
        self.timer.timeout.connect(self.tick)
        self.bar = None

    def start(self):
        self.bar = StepBar(self)
        self.bar.show()
        self.timer.start()

    def tick(self):
        if not self.recorder.feed(bool(self.button_state()), self.clock()):
            return
        if self.own_window():
            return
        try:
            shot = self.grab()
        except Exception:
            return
        if shot is None:
            return
        image, x, y, scale = shot
        self.recorder.add(image, x, y, scale, foreground_title())
        if self.bar is not None:
            self.bar.update_count(len(self.recorder.steps))

    def stop(self):
        self.timer.stop()
        if self.bar is not None:
            self.bar.close()
            self.bar.deleteLater()
            self.bar = None

    def finish(self):
        self.stop()
        self.on_finish(list(self.recorder.steps))

    def cancel(self):
        self.stop()
        if self.on_cancel:
            self.on_cancel()


def click_on_own_window():
    from PySide6.QtGui import QCursor
    from PySide6.QtWidgets import QApplication
    return QApplication.widgetAt(QCursor.pos()) is not None


def grab_under_cursor():
    """(Bild, x, y, Maßstab) des Bildschirms unter dem Mauszeiger; schnell per Qt, damit der Zustand vor dem Klick drauf ist."""
    from PySide6.QtGui import QCursor, QGuiApplication
    from .editor_items import qimage_to_pil
    pos = QCursor.pos()
    screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
    if screen is None:
        return None
    pixmap = screen.grabWindow(0)
    if pixmap.isNull():
        return None
    image = qimage_to_pil(pixmap.toImage())
    geometry = screen.geometry()
    scale = image.width / max(1, geometry.width())
    return image, round((pos.x() - geometry.x()) * scale), round((pos.y() - geometry.y()) * scale), scale


def exclude_from_capture(widget):
    """Windows 10 2004+: das Fenster erscheint nicht in Bildschirmfotos und Videos (WDA_EXCLUDEFROMCAPTURE)."""
    if sys.platform != "win32":
        return False
    import ctypes
    try:
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), 0x11))
    except (AttributeError, OSError):
        return False


def _bar_class():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton, QApplication

    class _StepBar(QWidget):
        def __init__(self, session):
            super().__init__()
            self.session = session
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool |
                                Qt.WindowType.WindowDoesNotAcceptFocus)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self.setStyleSheet("QWidget{background:#1f2933;color:#f0f4f8;font-size:13px} QPushButton{padding:4px 10px;border-radius:4px;background:#3b4754}"
                               " QPushButton#finish{background:#e5332a}")
            layout = QHBoxLayout(self)
            layout.setContentsMargins(10, 6, 6, 6)
            self.label = QLabel()
            layout.addWidget(self.label)
            finish = QPushButton("Fertig")
            finish.setObjectName("finish")
            finish.clicked.connect(session.finish)
            cancel = QPushButton("Abbrechen")
            cancel.clicked.connect(session.cancel)
            layout.addWidget(finish)
            layout.addWidget(cancel)
            self.update_count(0)
            self.adjustSize()
            screen = QApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                self.move(area.center().x() - self.width() // 2, area.top() + 12)

        def showEvent(self, event):
            super().showEvent(event)
            exclude_from_capture(self)

        def update_count(self, count):
            self.label.setText(f"● Schritt-Anleitung · {count} Schritt{'e' if count != 1 else ''} · jeder Klick wird festgehalten")
            self.adjustSize()

    return _StepBar


def StepBar(session):
    return _bar_class()(session)
