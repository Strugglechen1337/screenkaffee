"""Small vector tool icons drawn at runtime (no image files, crisp at any DPI)."""
import math
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

SIZE = 28


def _pen(color, width=2.0):
    return QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)


def _poly(*points):
    return QPolygonF([QPointF(x, y) for x, y in points])


def _draw(name, p, c):
    p.setPen(_pen(c))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if name == "select":
        p.setBrush(QColor(c))
        p.drawPolygon(_poly((8, 5), (8, 22), (13, 17.5), (16.5, 24), (19, 22.8), (15.6, 16.4), (22, 16)))
    elif name == "arrow":
        p.drawLine(QPointF(6, 22), QPointF(21, 7))
        p.setBrush(QColor(c))
        p.drawPolygon(_poly((23, 5), (15, 8), (20, 13)))
    elif name == "line":
        p.drawLine(QPointF(6, 22), QPointF(22, 6))
    elif name == "rectangle":
        p.drawRoundedRect(QRectF(5, 8, 18, 12), 2, 2)
    elif name == "ellipse":
        p.drawEllipse(QRectF(5, 7, 18, 14))
    elif name == "text":
        p.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        p.drawText(QRectF(0, 0, SIZE, SIZE), Qt.AlignmentFlag.AlignCenter, "T")
    elif name == "speech":
        p.drawRoundedRect(QRectF(5, 6, 18, 12), 4, 4)
        p.setBrush(QColor(c))
        p.drawPolygon(_poly((9, 18), (14, 18), (8, 24)))
    elif name == "step":
        p.setBrush(QColor(c))
        p.drawEllipse(QRectF(5, 5, 18, 18))
        p.setPen(QColor("#0f1522"))
        p.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        p.drawText(QRectF(5, 5, 18, 18), Qt.AlignmentFlag.AlignCenter, "1")
    elif name == "highlight":
        marker = QColor(c)
        marker.setAlpha(150)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(marker)
        p.drawRoundedRect(QRectF(4, 10, 20, 9), 2, 2)
    elif name == "freehand":
        path = QPainterPath(QPointF(5, 20))
        path.cubicTo(9, 4, 13, 26, 17, 12)
        path.cubicTo(19, 6, 22, 10, 23, 8)
        p.drawPath(path)
    elif name == "polygon":
        p.drawPolygon(_poly((6, 21), (9, 8), (20, 6), (23, 17), (14, 23)))
    elif name == "bracket":
        path = QPainterPath(QPointF(20, 5))
        path.cubicTo(8, 5, 18, 14, 8, 14)
        path.cubicTo(18, 14, 8, 23, 20, 23)
        p.drawPath(path)
    elif name == "spotlight":
        path = QPainterPath()
        path.addRoundedRect(QRectF(3, 4, 22, 20), 3, 3)
        path.addEllipse(QRectF(9, 9, 10, 10))
        path.setFillRule(Qt.FillRule.OddEvenFill)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c))
        p.drawPath(path)
    elif name == "magnifier":
        p.drawEllipse(QRectF(5, 5, 14, 14))
        p.drawLine(QPointF(17, 17), QPointF(23, 23))
    elif name in ("blur", "pixelate"):
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            for j in range(3):
                shade = QColor(c)
                shade.setAlpha(80 + 80 * ((i + j) % 2) if name == "pixelate" else 255 - 55 * (i + j))
                p.setBrush(shade)
                p.drawRect(QRectF(5 + i * 6.3, 5 + j * 6.3, 5.6, 5.6))
    elif name == "redact":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c))
        p.drawRoundedRect(QRectF(4, 9, 20, 10), 2, 2)
    elif name == "crop":
        p.drawLine(QPointF(9, 4), QPointF(9, 19))
        p.drawLine(QPointF(9, 19), QPointF(24, 19))
        p.drawLine(QPointF(4, 9), QPointF(19, 9))
        p.drawLine(QPointF(19, 9), QPointF(19, 24))
    elif name == "measure":
        p.drawRect(QRectF(3, 10, 22, 8))
        for i, x in enumerate(range(6, 24, 3)):
            p.drawLine(QPointF(x, 10), QPointF(x, 13 if i % 2 else 15))
    elif name == "picker":
        p.drawLine(QPointF(8, 20), QPointF(18, 10))
        p.drawEllipse(QRectF(16, 4, 7, 7))
        p.drawLine(QPointF(7, 21), QPointF(6, 24))
    else:
        p.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        p.drawText(QRectF(0, 0, SIZE, SIZE), Qt.AlignmentFlag.AlignCenter, name[:1].upper())


def tool_icon(name, color="#dbe5f3", active="#67e2c3"):
    icon = QIcon()
    for mode, tone in ((QIcon.Mode.Normal, color), (QIcon.Mode.Active, active)):
        for state, shade in ((QIcon.State.Off, tone), (QIcon.State.On, active)):
            scale = 2
            pixmap = QPixmap(SIZE * scale, SIZE * scale)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.scale(scale, scale)
            _draw(name, painter, shade)
            painter.end()
            icon.addPixmap(pixmap, mode, state)
    return icon


def command_icon(name, color):
    """Original compact command glyphs on colored discs."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(2, 2)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(1, 1, 30, 30))
    p.translate(2, 2)
    p.setPen(_pen("#ffffff", 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if name == "save":
        p.drawRoundedRect(QRectF(6, 5, 16, 18), 1, 1)
        p.drawRect(QRectF(10, 5, 8, 6))
        p.drawRect(QRectF(10, 16, 8, 7))
    elif name == "copy":
        p.drawRoundedRect(QRectF(9, 8, 13, 16), 1, 1)
        p.drawPolyline(_poly((6, 20), (5, 20), (5, 4), (18, 4), (18, 5)))
    elif name in ("undo", "redo"):
        if name == "redo":
            p.translate(28, 0)
            p.scale(-1, 1)
        p.drawArc(QRectF(7, 8, 15, 15), -60 * 16, 250 * 16)
        p.drawPolyline(_poly((5, 5), (5, 13), (13, 13)))
    elif name == "image":
        p.drawRect(QRectF(5, 6, 18, 16))
        p.drawEllipse(QRectF(9, 9, 3, 3))
        p.drawPolyline(_poly((6, 20), (12, 15), (16, 18), (22, 12)))
    elif name == "effects":
        p.drawRect(QRectF(6, 6, 16, 16))
        p.drawLine(QPointF(9, 9), QPointF(19, 19))
        p.drawLine(QPointF(19, 9), QPointF(9, 19))
    elif name == "print":
        p.drawRect(QRectF(9, 5, 10, 6))
        p.drawRoundedRect(QRectF(5, 11, 18, 9), 1, 1)
        p.drawRect(QRectF(9, 16, 10, 7))
    elif name == "cutout":
        p.drawLine(QPointF(5, 8), QPointF(23, 8))
        p.drawLine(QPointF(5, 20), QPointF(23, 20))
        p.drawLine(QPointF(8, 11), QPointF(20, 17))
        p.drawLine(QPointF(20, 11), QPointF(8, 17))
    elif name == "properties":
        for y in (8, 14, 20):
            p.drawLine(QPointF(10, y), QPointF(22, y))
            p.drawPoint(QPointF(6, y))
    elif name == "fit":
        p.drawRect(QRectF(5, 7, 18, 14))
        p.drawLine(QPointF(9, 14), QPointF(19, 14))
        p.drawPolyline(_poly((11, 11), (8, 14), (11, 17)))
        p.drawPolyline(_poly((17, 11), (20, 14), (17, 17)))
    elif name == "canvas":
        p.drawRect(QRectF(8, 8, 12, 12))
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            p.drawLine(QPointF(14 + dx * 6, 14 + dy * 6), QPointF(14 + dx * 10, 14 + dy * 10))
    elif name == "border":
        p.setPen(_pen("#ffffff", 2.4))
        p.drawRoundedRect(QRectF(6, 6, 16, 16), 2, 2)
    elif name in ("prev", "next"):
        if name == "next":
            p.translate(28, 0)
            p.scale(-1, 1)
        p.drawLine(QPointF(20, 14), QPointF(8, 14))
        p.drawPolyline(_poly((13, 9), (8, 14), (13, 19)))
    elif name in ("rotate_right", "rotate_left"):
        if name == "rotate_left":
            p.translate(28, 0)
            p.scale(-1, 1)
        p.drawArc(QRectF(6, 6, 16, 16), 30 * 16, 240 * 16)
        p.drawPolyline(_poly((18, 5), (22, 9), (16, 11)))
    elif name == "zoom_100":
        p.drawEllipse(QRectF(5, 5, 14, 14))
        p.drawLine(QPointF(17, 17), QPointF(23, 23))
        p.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        p.drawText(QRectF(5, 5, 14, 14), Qt.AlignmentFlag.AlignCenter, "1:1")
    else:
        _draw(name, p, "#ffffff")
    p.end()
    return QIcon(pix)


def _glyph(name, p, c):
    """Glyphs for the round action buttons, drawn on a 44x44 canvas."""
    p.setPen(_pen(c, 2.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if name == "trash":
        # Deckel, Griff und konischer Korb bleiben auch klein gut erkennbar.
        p.drawLine(QPointF(11, 13), QPointF(33, 13))
        p.drawRoundedRect(QRectF(18, 8, 8, 5), 1.5, 1.5)
        path = QPainterPath(QPointF(14, 17))
        path.lineTo(16, 33)
        path.quadTo(16.2, 35, 19, 35)
        path.lineTo(25, 35)
        path.quadTo(27.8, 35, 28, 33)
        path.lineTo(30, 17)
        p.drawPath(path)
        p.drawLine(QPointF(20, 20), QPointF(20.8, 30))
        p.drawLine(QPointF(24, 20), QPointF(23.2, 30))
    elif name == "restore":
        p.drawArc(QRectF(11, 12, 23, 23), -65 * 16, 285 * 16)
        p.drawPolyline(_poly((10, 10), (10, 20), (20, 20)))
    elif name == "camera":
        p.drawRoundedRect(QRectF(11, 15, 22, 15), 3, 3)
        p.drawEllipse(QRectF(17.5, 18.5, 9, 9))
        p.drawLine(QPointF(17, 15), QPointF(19, 12))
        p.drawLine(QPointF(19, 12), QPointF(25, 12))
        p.drawLine(QPointF(25, 12), QPointF(27, 15))
    elif name == "edit":
        p.drawLine(QPointF(13, 31), QPointF(29, 15))
        p.drawLine(QPointF(29, 15), QPointF(32, 18))
        p.drawLine(QPointF(32, 18), QPointF(16, 34))
        p.drawLine(QPointF(13, 31), QPointF(16, 34))
        p.drawLine(QPointF(12, 12), QPointF(20, 20))
        p.drawLine(QPointF(12, 12), QPointF(12, 18))
        p.drawLine(QPointF(12, 12), QPointF(18, 12))
    elif name == "copy":
        p.drawRoundedRect(QRectF(15, 12, 16, 20), 2, 2)
        p.drawRoundedRect(QRectF(11, 16, 16, 20), 2, 2)
    elif name == "export":
        p.drawLine(QPointF(22, 28), QPointF(22, 12))
        p.drawPolyline(_poly((15, 19), (22, 12), (29, 19)))
        p.drawPolyline(_poly((12, 26), (12, 33), (32, 33), (32, 26)))
    elif name == "document":
        p.drawPolyline(_poly((14, 10), (26, 10), (32, 16), (32, 34), (14, 34), (14, 10)))
        p.drawLine(QPointF(18, 22), QPointF(28, 22))
        p.drawLine(QPointF(18, 27), QPointF(28, 27))
    elif name == "scan":
        p.drawPolyline(_poly((12, 18), (12, 12), (18, 12)))
        p.drawPolyline(_poly((26, 12), (32, 12), (32, 18)))
        p.drawPolyline(_poly((32, 26), (32, 32), (26, 32)))
        p.drawPolyline(_poly((18, 32), (12, 32), (12, 26)))
        p.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        p.drawText(QRectF(12, 12, 20, 20), Qt.AlignmentFlag.AlignCenter, "T")
    elif name == "more":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c))
        for x in (14, 22, 30):
            p.drawEllipse(QRectF(x - 2.6, 19.4, 5.2, 5.2))
    elif name == "share":
        for x, y in ((14, 22), (29, 13), (29, 31)):
            p.drawEllipse(QRectF(x - 3.6, y - 3.6, 7.2, 7.2))
        p.drawLine(QPointF(17.3, 20.2), QPointF(25.7, 15.0))
        p.drawLine(QPointF(17.3, 23.8), QPointF(25.7, 29.0))
    elif name == "close":
        p.drawLine(QPointF(14, 14), QPointF(30, 30))
        p.drawLine(QPointF(30, 14), QPointF(14, 30))
    elif name == "video":
        p.setBrush(QColor(c))
        p.drawEllipse(QRectF(14, 14, 16, 16))


def round_icon(name, ring="#e5332a", size=44):
    """A ring button icon: dark disc, colored ring, colored glyph."""
    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(scale * size / 44, scale * size / 44)
    painter.setPen(_pen(ring, 3))
    painter.setBrush(QColor("#2f2f2f"))
    painter.drawEllipse(QRectF(3, 3, 38, 38))
    _glyph(name, painter, ring)
    painter.end()
    return QIcon(pixmap)


def header_icon(name, color="#d0d0d0"):
    """Small 20x20 header icon for workspace toggle and settings."""
    pix = QPixmap(40, 40)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(2, 2)
    p.setPen(_pen(color, 1.8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if name == "workspaces":
        p.drawRoundedRect(QRectF(2, 3, 16, 14), 2, 2)
        p.drawLine(QPointF(7, 3), QPointF(7, 17))
    elif name == "settings":
        p.drawEllipse(QRectF(6, 6, 8, 8))
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            p.drawLine(QPointF(10 + 4.2 * math.cos(rad), 10 + 4.2 * math.sin(rad)),
                       QPointF(10 + 7.2 * math.cos(rad), 10 + 7.2 * math.sin(rad)))
    elif name == "import":
        p.drawLine(QPointF(10, 3), QPointF(10, 13))
        p.drawPolyline(_poly((6, 9), (10, 13), (14, 9)))
        p.drawPolyline(_poly((3, 12), (3, 17), (17, 17), (17, 12)))
    elif name == "search":
        p.drawEllipse(QRectF(3, 3, 10, 10))
        p.drawLine(QPointF(11.5, 11.5), QPointF(17, 17))
    p.end()
    return QIcon(pix)


def styled_thumbnail(pixmap, kind="image", width=164, height=104, favorite=False):
    """Thumbnail card with subtle shadow, white border, and media badge for videos/documents."""
    dpr = 2
    canvas = QPixmap(width * dpr, height * dpr)
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(Qt.GlobalColor.transparent)
    p = QPainter(canvas)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    if pixmap.isNull():
        pixmap = QPixmap((width - 16) * dpr, (height - 16) * dpr)
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(QColor("#383838"))
    scaled = pixmap.scaled((width - 16) * dpr, (height - 16) * dpr, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    scaled.setDevicePixelRatio(dpr)
    sw, sh = scaled.width() / dpr, scaled.height() / dpr
    x = (width - sw) / 2
    y = (height - sh) / 2
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 95))
    p.drawRoundedRect(QRectF(x - 1, y + 1, sw + 6, sh + 6), 2, 2)
    p.setBrush(QColor("#ffffff"))
    p.drawRect(QRectF(x - 2, y - 2, sw + 4, sh + 4))
    p.drawPixmap(QPointF(x, y), scaled)
    if kind == "video":
        cx, cy = width / 2, height / 2
        p.setBrush(QColor(25, 25, 25, 190))
        p.setPen(_pen("#ffffff", 2.0))
        p.drawEllipse(QRectF(cx - 17, cy - 17, 34, 34))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawPolygon(_poly((cx - 5, cy - 9), (cx - 5, cy + 9), (cx + 10, cy)))
    elif kind == "document":
        cx, cy = width / 2, height / 2
        p.setBrush(QColor(35, 35, 35, 200))
        p.setPen(_pen("#ffffff", 1.8))
        p.drawRoundedRect(QRectF(cx - 16, cy - 20, 32, 40), 3, 3)
        for dy in (-6, 1, 8):
            p.drawLine(QPointF(cx - 9, cy + dy), QPointF(cx + 9, cy + dy))
    if favorite:
        sx, sy = x + sw - 12, y + 12
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(25, 25, 25, 210))
        p.drawEllipse(QRectF(sx - 10, sy - 10, 20, 20))
        p.setBrush(QColor("#f5b82e"))
        star = []
        for idx in range(10):
            ang = math.radians(-90 + idx * 36)
            r = 7.5 if idx % 2 == 0 else 3.2
            star.append((sx + r * math.cos(ang), sy + r * math.sin(ang)))
        p.drawPolygon(_poly(*star))
    p.end()
    return QIcon(canvas)
