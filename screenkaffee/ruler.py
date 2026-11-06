"""Lineal: Abstände in Pixeln messen – im Aufnahme-Overlay (Taste L oder „Bildschirm ausmessen“) und im Editor."""
import math
from .i18n import tr

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QPen

LINE = "#ff3d8b"


def measurement(x1, y1, x2, y2):
    """Länge, Breite, Höhe und Winkel (0° = waagerecht nach rechts, positiv = nach oben) in Bildpixeln."""
    dx, dy = x2 - x1, y2 - y1
    return {"length": math.hypot(dx, dy), "dx": abs(dx), "dy": abs(dy),
            "angle": (math.degrees(math.atan2(-dy, dx)) + 360) % 360}


def snap_axis(x1, y1, x2, y2):
    """Mit Umschalt: auf waagerecht, senkrecht oder 45° einrasten."""
    dx, dy = x2 - x1, y2 - y1
    if abs(dx) > 2.4 * abs(dy):
        return x2, y1
    if abs(dy) > 2.4 * abs(dx):
        return x1, y2
    side = max(abs(dx), abs(dy))
    return x1 + math.copysign(side, dx or 1), y1 + math.copysign(side, dy or 1)


def label(values):
    text = tr('{length} px  ·  B {width}  ·  H {height}', length=round(values['length']), width=round(values['dx']), height=round(values['dy']))
    if values["dx"] and values["dy"]:
        incline = math.degrees(math.atan2(values["dy"], values["dx"]))         # Neigung gegen die Waagerechte, 0–90°
        text += f"  ·  {incline:.1f}°".replace(".", ",")
    return text


def clipboard_text(values):
    return tr('{width} × {height} px (Länge {length} px)', width=round(values['dx']), height=round(values['dy']), length=round(values['length']))


def paint(painter, start, end, values, pixel_scale=1.0, guides=None):
    """Messlinie mit Endmarken, Hilfslinien durch beide Endpunkte (``guides`` = Zeichenfläche) und Beschriftung.
    ``pixel_scale``: wie viele Painter-Einheiten ein Bildschirmpixel sind (für gleich dicke Linien bei Zoom)."""
    painter.save()
    if guides is not None:
        painter.setPen(QPen(QColor(255, 61, 139, 110), 1 * pixel_scale, Qt.PenStyle.DashLine))
        for point in (start, end):
            painter.drawLine(QPointF(guides.left(), point.y()), QPointF(guides.right(), point.y()))
            painter.drawLine(QPointF(point.x(), guides.top()), QPointF(point.x(), guides.bottom()))
    painter.setPen(QPen(QColor(LINE), 2 * pixel_scale))
    painter.drawLine(start, end)
    tick = 6 * pixel_scale
    length = math.hypot(end.x() - start.x(), end.y() - start.y()) or 1
    nx, ny = -(end.y() - start.y()) / length * tick, (end.x() - start.x()) / length * tick
    for point in (start, end):
        painter.drawLine(QPointF(point.x() - nx, point.y() - ny), QPointF(point.x() + nx, point.y() + ny))
    font = QFont("Segoe UI")
    font.setPixelSize(max(1, round(12 * pixel_scale)))
    font.setBold(True)
    painter.setFont(font)
    text = label(values)
    metrics = painter.fontMetrics()
    width, height = metrics.horizontalAdvance(text) + 14 * pixel_scale, metrics.height() + 6 * pixel_scale
    middle = QPointF((start.x() + end.x()) / 2, (start.y() + end.y()) / 2)
    box = QRectF(middle.x() - width / 2, middle.y() - height - 10 * pixel_scale, width, height)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(20, 24, 30, 225))
    painter.drawRoundedRect(box, 5 * pixel_scale, 5 * pixel_scale)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
    painter.restore()
