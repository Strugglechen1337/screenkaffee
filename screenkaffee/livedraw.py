"""Live-Zeichnen: Pfeile, Kreise und Freihand direkt auf den Bildschirm – für Videoaufnahmen und Präsentationen.

Je Bildschirm ein durchsichtiges Fenster ganz oben. Solange es aktiv ist, gehen Mausklicks an das Zeichnen
(nicht an die Programme darunter). Striche bleiben ``hold`` Sekunden stehen und verblassen dann in ``fade``
Sekunden; ``hold = 0`` heißt „bleibt stehen“. Esc oder dasselbe Tastenkürzel beendet.
"""
import math
import time
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, QPointF, QRectF, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget

COLORS = {"1": "#e5332a", "2": "#ffcc00", "3": "#22c55e", "4": "#3b82f6", "5": "#ffffff"}
TOOLS = {"p": "pen", "a": "arrow", "r": "rectangle", "e": "ellipse", "m": "marker"}
TOOL_NAMES = {"pen": "Stift", "arrow": "Pfeil", "rectangle": "Rechteck", "ellipse": "Ellipse", "marker": "Marker"}
HINT = "Live-Zeichnen · P Stift · A Pfeil · R Rechteck · E Ellipse · M Marker · 1–5 Farbe · H stehen lassen · Rück rückgängig · Entf alles weg · Esc beenden"


@dataclass
class Stroke:
    tool: str
    color: str
    width: float
    points: list = field(default_factory=list)
    finished: float = None             # Zeitpunkt des Loslassens; None = wird noch gezeichnet


def opacity(stroke, now, hold, fade):
    """1 solange gezeichnet wird oder innerhalb von ``hold``; danach linear bis 0 über ``fade`` Sekunden."""
    if stroke.finished is None or hold <= 0:
        return 1.0
    age = now - stroke.finished - hold
    if age <= 0:
        return 1.0
    return max(0.0, 1.0 - age / max(fade, .01))


def arrow_head(start, end, size):
    """Zwei Punkte der Pfeilspitze für eine Linie start→end."""
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    return [(end[0] - size * math.cos(angle - math.pi / 7), end[1] - size * math.sin(angle - math.pi / 7)),
            (end[0] - size * math.cos(angle + math.pi / 7), end[1] - size * math.sin(angle + math.pi / 7))]


class DrawState:
    """Gemeinsamer Zustand aller Bildschirme: Werkzeug, Farbe, Haltezeit."""

    def __init__(self, hold=3.0, fade=1.0, tool="arrow", color="#e5332a", width=5):
        self.hold, self.fade, self.tool, self.color, self.width = hold, fade, tool, color, width


class LiveDrawWindow(QWidget):
    def __init__(self, screen, state, session, clock=time.monotonic):
        super().__init__()
        self.state, self.session, self.clock = state, session, clock
        self.strokes = []
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setGeometry(screen.geometry())
        self.hint_until = clock() + 5
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def tick(self):
        now = self.clock()
        before = len(self.strokes)
        self.strokes = [s for s in self.strokes if opacity(s, now, self.state.hold, self.state.fade) > 0]
        if before or now < self.hint_until + 1:
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.undo()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        width = self.state.width * (4 if self.state.tool == "marker" else 1)
        point = event.position()
        start = (point.x(), point.y())
        points = [start] if self.state.tool in ("pen", "marker") else [start, start]
        self.strokes.append(Stroke(self.state.tool, self.state.color, width, points))
        self.update()

    def mouseMoveEvent(self, event):
        if not self.strokes or self.strokes[-1].finished is not None or not event.buttons() & Qt.MouseButton.LeftButton:
            return
        stroke, point = self.strokes[-1], (event.position().x(), event.position().y())
        if stroke.tool in ("pen", "marker"):
            stroke.points.append(point)
        else:
            stroke.points[-1] = point
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.strokes and self.strokes[-1].finished is None:
            self.strokes[-1].finished = self.clock()
            self.update()

    def keyPressEvent(self, event):
        self.session.key(event)

    def undo(self):
        if self.strokes:
            self.strokes.pop()
            self.update()

    def clear(self):
        self.strokes.clear()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Fast unsichtbar, aber nicht ganz durchsichtig: sonst reicht Windows die Klicks an das Fenster darunter weiter.
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
        now = self.clock()
        for stroke in self.strokes:
            alpha = opacity(stroke, now, self.state.hold, self.state.fade)
            color = QColor(stroke.color)
            color.setAlphaF(alpha * (.4 if stroke.tool == "marker" else 1))
            pen = QPen(color, stroke.width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            (x1, y1), (x2, y2) = stroke.points[0], stroke.points[-1]
            if stroke.tool in ("pen", "marker"):
                path = QPainterPath(QPointF(*stroke.points[0]))
                for point in stroke.points[1:]:
                    path.lineTo(QPointF(*point))
                painter.drawPath(path)
            elif stroke.tool == "arrow":
                painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
                if math.hypot(x2 - x1, y2 - y1) > 4:
                    head = arrow_head((x1, y1), (x2, y2), 10 + stroke.width * 3)
                    painter.setBrush(color)
                    painter.drawPolygon(QPolygonF([QPointF(x2, y2)] + [QPointF(*p) for p in head]))
            elif stroke.tool == "rectangle":
                painter.drawRoundedRect(QRectF(QPointF(x1, y1), QPointF(x2, y2)).normalized(), 6, 6)
            elif stroke.tool == "ellipse":
                painter.drawEllipse(QRectF(QPointF(x1, y1), QPointF(x2, y2)).normalized())
        if now < self.hint_until + 1:
            alpha = max(0.0, min(1.0, self.hint_until + 1 - now))
            painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
            stay = "bleibt stehen" if self.state.hold <= 0 else f"verblasst nach {self.state.hold:g} s"
            text = HINT + f"  ·  {TOOL_NAMES[self.state.tool]}, {stay}"
            box = painter.fontMetrics().boundingRect(text).adjusted(-12, -7, 12, 7)
            box.moveCenter(self.rect().center())
            box.moveTop(16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(20, 24, 30, round(220 * alpha)))
            painter.drawRoundedRect(box, 8, 8)
            painter.setPen(QColor(255, 255, 255, round(255 * alpha)))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


class LiveDrawSession:
    def __init__(self, hold=3.0, on_end=None, screens=None, clock=time.monotonic):
        self.state = DrawState(hold=hold)
        self.on_end = on_end
        self.windows = [LiveDrawWindow(screen, self.state, self, clock) for screen in (screens or QApplication.screens())]

    def start(self):
        for window in self.windows:
            window.show()
            window.raise_()
        if self.windows:
            self.windows[0].activateWindow()

    def show_hint(self):
        for window in self.windows:
            window.hint_until = window.clock() + 2
            window.update()

    def key(self, event):
        key, text = event.key(), event.text().lower()
        if key == Qt.Key.Key_Escape:
            self.stop()
        elif key == Qt.Key.Key_Backspace or (key == Qt.Key.Key_Z and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            latest = max((w for w in self.windows if w.strokes), key=lambda w: w.strokes[-1].finished or 1e18, default=None)
            if latest is not None:
                latest.undo()
        elif key == Qt.Key.Key_Delete:
            for window in self.windows:
                window.clear()
        elif text == "h":
            self.state.hold = 0.0 if self.state.hold > 0 else 3.0
            self.show_hint()
        elif text in COLORS:
            self.state.color = COLORS[text]
        elif text in TOOLS:
            self.state.tool = TOOLS[text]
            self.show_hint()

    def stop(self):
        for window in self.windows:
            window.timer.stop()
            window.close()
            window.deleteLater()
        self.windows = []
        if self.on_end:
            self.on_end()

    @property
    def active(self):
        return bool(self.windows)
