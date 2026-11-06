"""Zeichenobjekte (Annotation) und Geometrie-Helfer des Editors."""
import logging
from .i18n import tr
import json
import math
import base64
import io
from pathlib import Path
from PIL import Image, ImageFilter
from PySide6.QtCore import Qt, QPointF, QRectF, Signal, Slot, QThread
from PySide6.QtGui import (QColor, QImage, QPainter, QPen, QBrush, QFont,
    QPixmap, QPolygonF, QTransform, QKeySequence, QShortcut, QUndoCommand, QUndoStack, QPainterPath)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGraphicsScene, QGraphicsView, QGraphicsItem, QToolBar, QColorDialog,
    QInputDialog, QLabel, QSpinBox, QComboBox, QFileDialog, QMessageBox,
    QDialog, QDialogButtonBox, QFormLayout, QCheckBox, QDoubleSpinBox, QLineEdit, QMenu, QToolButton)
from .color import save_png, save_image, srgb_profile
from .richtext import RichTextDialog, base_font, draw_text


LOG = logging.getLogger(__name__)


def pil_to_qimage(image):
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    return QImage(rgba.tobytes(), rgba.width, rgba.height, rgba.width * 4,
                  QImage.Format.Format_RGBA8888).copy()


def qimage_to_pil(image, keep_alpha=False):
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombytes("RGBA", (image.width(), image.height()),
        bytes(image.bits()), "raw", "RGBA", image.bytesPerLine()).convert("RGBA" if keep_alpha else "RGB")


def step_label(number, step_format="1"):
    """Label of a numbered step: "1" (Arabic), "A" (letters, A..Z, AA..) or "I" (Roman numerals)."""
    if step_format == "A" and number >= 1:
        label = ""
        while number:
            number, rest = divmod(number - 1, 26)
            label = chr(65 + rest) + label
        return label
    if step_format == "I" and 1 <= number < 4000:
        label = ""
        for value, symbol in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                              (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
            while number >= value:
                label += symbol
                number -= value
        return label
    return str(number)


def line_geometry(points, curve=0):
    """Start, end and optional quadratic control point; curve is the bend in pixels."""
    x1, y1, x2, y2 = points
    start, end = QPointF(x1, y1), QPointF(x2, y2)
    if not curve:
        return start, end, None
    length = math.hypot(x2 - x1, y2 - y1) or 1
    mid = (start + end) / 2
    return start, end, QPointF(mid.x() - (y2 - y1) / length * curve * 2, mid.y() + (x2 - x1) / length * curve * 2)


def keep_aspect(x1, y1, old_x2, old_y2, x2, y2):
    """Scale the new corner so width/height keep the ratio they had before the drag."""
    old_w, old_h = old_x2 - x1, old_y2 - y1
    if not old_w or not old_h:
        return x2, y2
    scale = max(abs(x2 - x1) / abs(old_w), abs(y2 - y1) / abs(old_h))
    return x1 + old_w * scale, y1 + old_h * scale


def resize_annotation(data, old, new):
    sx, sy = new.width() / max(1, old.width()), new.height() / max(1, old.height())
    def point(x, y):
        return [new.left() + (x - old.left()) * sx, new.top() + (y - old.top()) * sy]
    result = dict(data)
    result["points"] = point(*data["points"][:2]) + point(*data["points"][2:])
    if data["kind"] in ("text", "speech", "step"):
        result["points"][:2] = [new.left() + 4, new.top() + 4]
        result["text_width"], result["text_height"] = new.width(), new.height()
    if "path" in data:
        result["path"] = [point(x, y) for x, y in data["path"]]
    if "tail" in data:
        result["tail"] = point(*data["tail"])
    return result


class Annotation(QGraphicsItem):
    def __init__(self, data, view):
        super().__init__()
        self.data, self.view = dict(data), view
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setPos(*data.get("pos", [0, 0]))
        self.original_pos = self.pos()
        self.resize_before = None
        self.resize_handle = None
        self.tail_before = None
        self.setTransformOriginPoint(self.body_rect().center())
        self.setRotation(data.get("rotation", 0))
        self.apply_lock()

    def locked(self):
        return bool(self.data.get("locked"))

    def apply_lock(self):
        """Gesperrte Markierungen bleiben auswählbar (für Kontextmenü/Entsperren), lassen sich aber nicht ziehen."""
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not self.locked())

    def sample_box(self):
        """Quellbereich einer Lupe in Bildkoordinaten (gesperrt oder unter dem Glas)."""
        rect = self.body_rect()
        zoom = max(1, self.data.get("zoom", 2))
        center = rect.center() + self.pos()
        return list(self.data.get("sample_box", [center.x() - rect.width() / zoom / 2,
            center.y() - rect.height() / zoom / 2, center.x() + rect.width() / zoom / 2,
            center.y() + rect.height() / zoom / 2]))

    def magnifier_link(self):
        """Rahmen um den Quellbereich und zwei Verbindungslinien zum Glas, in Item-Koordinaten.
        Liefert (QRectF, [(Start, Ende), ...]) oder None, wenn die Option aus ist."""
        if self.data.get("kind") != "magnifier" or not self.data.get("magnifier_lines"):
            return None
        x1, y1, x2, y2 = self.sample_box()
        box = QRectF(self.mapFromScene(QPointF(x1, y1)), self.mapFromScene(QPointF(x2, y2))).normalized()
        glass = self.body_rect()
        a, b = box.center(), glass.center()
        dx, dy = b.x() - a.x(), b.y() - a.y()
        distance = math.hypot(dx, dy)
        radius = min(glass.width(), glass.height()) / 2
        lines = []
        if distance > radius:                               # Quellbereich liegt neben dem Glas
            ux, uy = dx / distance, dy / distance
            nx, ny = -uy, ux                                # Senkrechte zur Verbindung der Mittelpunkte
            half = max(box.width(), box.height()) / 2
            for side in (1, -1):
                start = QPointF(a.x() + side * nx * half, a.y() + side * ny * half)
                # Endpunkt auf dem Glasrand, um 45° zur Verbindungslinie versetzt
                angle = math.atan2(-uy, -ux) + side * -math.pi / 4
                end = QPointF(b.x() + radius * math.cos(angle), b.y() + radius * math.sin(angle))
                lines.append((start, end))
        return box, lines

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.data.get("magnifier_lines"):
            self.prepareGeometryChange()        # Quellrahmen bleibt stehen, die Item-Grenzen wandern mit
        # Magnet: only while the user drags this item with the mouse, never for undo/redo or code moves.
        if (change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and getattr(self.view, "magnet", False)
                and self.scene() is not None and self.scene().mouseGrabberItem() is self):
            return self.view.snap_position(self, value)
        return super().itemChange(change, value)

    def body_rect(self):
        x1, y1, x2, y2 = self.data["points"]
        if self.data["kind"] in ("text", "step", "speech"):
            return QRectF(x1 - 4, y1 - 4, self.data.get("text_width", 300), self.data.get("text_height", 80))
        return QRectF(QPointF(x1, y1), QPointF(x2, y2)).normalized()

    def tail_tip(self):
        rect = self.body_rect().adjusted(8, 6, -8, -6)
        return QPointF(*self.data.get("tail", [rect.left() + 5, rect.bottom() + 20]))

    def handles(self):
        rect = self.body_rect().adjusted(-8, -8, 8, 8)
        return [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]

    def boundingRect(self):
        x1, y1, x2, y2 = self.data["points"]
        rect = QRectF(QPointF(x1, y1), QPointF(x2, y2)).normalized()
        if self.data["kind"] == "spotlight":
            return QRectF(-self.view.source.width * 2, -self.view.source.height * 2,
                          self.view.source.width * 5, self.view.source.height * 5)
        if self.data["kind"] == "step" and self.data.get("connect"):
            prev = self.previous_step_center()
            own = QRectF(x1 - 4, y1 - 4, self.data.get("text_width", 46), self.data.get("text_height", 46))
            return own.united(QRectF(prev - QPointF(2, 2), prev + QPointF(2, 2)).adjusted(-self.data["width"], -self.data["width"], self.data["width"], self.data["width"])) if prev else own
        if self.data["kind"] in ("text", "step", "speech"):
            rect = self.body_rect()
            if self.data["kind"] == "speech":
                rect = rect.united(QRectF(self.tail_tip(), self.tail_tip()).adjusted(-5, -5, 5, 5))
            margin = max(12, self.data.get("glow_radius", 0) * 3,
                         self.data.get("shadow_radius", 0) * 3 + abs(self.data.get("shadow_offset", 0)))
            return rect.adjusted(-margin, -margin, margin, margin)
        margin = max(22, self.data.get("shadow_radius", 0) * 3 + abs(self.data.get("shadow_offset", 0)))
        bounds = rect.adjusted(-margin, -margin, margin, margin)
        link = self.magnifier_link() if self.data["kind"] == "magnifier" else None
        if link is not None:
            pad = self.data.get("width", 4) + 2
            bounds = bounds.united(link[0].adjusted(-pad, -pad, pad, pad))
        return bounds

    def previous_step_center(self):
        """Center of the step numbered one lower, in this item's coordinates."""
        try:
            number = int(self.data.get("number") or self.data["text"]) - 1
        except (KeyError, ValueError):
            return None
        for item in self.view.annotations():
            if item is not self and item.data["kind"] == "step" and item.data.get("number", item.data.get("text")) in (number, str(number)):
                x, y = item.data["points"][:2]
                return self.mapFromScene(item.mapToScene(QPointF(x + 19, y + 19)))
        return None

    def shape(self):
        path = QPainterPath()
        if self.data["kind"] == "spotlight":
            x1, y1, x2, y2 = self.data["points"]
            path.addRect(QRectF(QPointF(x1, y1), QPointF(x2, y2)).normalized())
            if self.isSelected():
                for handle in self.handles():
                    path.addRect(QRectF(handle - QPointF(6, 6), handle + QPointF(6, 6)))
        elif self.data["kind"] == "magnifier":
            # Nur das Glas ist anklickbar, nicht der (evtl. entfernte) Quellbereich.
            path.addRect(self.body_rect().adjusted(-22, -22, 22, 22))
        else:
            path.addRect(self.boundingRect())
        return path

    def paint(self, painter, option, widget=None):
        d = self.data
        kind, color, width = d["kind"], QColor(d["color"]), d["width"]
        x1, y1, x2, y2 = d["points"]
        rect = QRectF(QPointF(x1, y1), QPointF(x2, y2)).normalized()
        shadow_r, shadow_off = d.get("shadow_radius", 0), d.get("shadow_offset", 0)
        if (shadow_r or shadow_off) and kind not in ("text", "speech", "spotlight", "highlight", "redact", "blur", "pixelate") and not getattr(self, "_painting_shadow", False):
            shift = shadow_off if shadow_off else 4
            area = self.boundingRect().toAlignedRect()
            if area.width() > 0 and area.height() > 0:
                mask = QImage(area.size(), QImage.Format.Format_RGBA8888)
                mask.fill(Qt.GlobalColor.transparent)
                ink = QPainter(mask)
                ink.setRenderHint(QPainter.RenderHint.Antialiasing)
                ink.translate(-area.x(), -area.y())
                self._painting_shadow = True
                try:
                    self.paint(ink, option, widget)
                finally:
                    self._painting_shadow = False
                    ink.end()
                alpha = qimage_to_pil(mask, keep_alpha=True).getchannel("A")
                if shadow_r:
                    alpha = alpha.filter(ImageFilter.GaussianBlur(shadow_r))
                shade = Image.new("RGBA", alpha.size, QColor(d.get("shadow_color", "#000000")).getRgb())
                shade.putalpha(alpha.point(lambda a: a * 180 // 255))
                painter.drawImage(QPointF(area.x() + shift, area.y() + shift), pil_to_qimage(shade))
        pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setOpacity(painter.opacity() * d.get("opacity", 1))
        painter.setBrush(QColor(d["fill"]) if d.get("fill") else Qt.BrushStyle.NoBrush)
        if kind in ("rectangle", "ellipse"):
            if kind == "rectangle":
                painter.drawRoundedRect(rect, d.get("radius", 0), d.get("radius", 0))
            else:
                painter.drawEllipse(rect)
        elif kind in ("arrow", "line"):
            pen.setStyle({"dash": Qt.PenStyle.DashLine, "dot": Qt.PenStyle.DotLine}.get(d.get("line_style"), Qt.PenStyle.SolidLine))
            painter.setPen(pen)
            start, end, control = line_geometry(d["points"], d.get("curve", 0))
            path = QPainterPath(start)
            if control is None:
                path.lineTo(end)
            else:
                path.quadTo(control, end)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            if kind == "arrow":
                painter.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                size = max(13, width * 4)
                style = d.get("arrow_style", "filled")
                heads = [(end, math.atan2(end.y() - (control or start).y(), end.x() - (control or start).x()))]
                if style == "double":
                    heads.append((start, math.atan2(start.y() - (control or end).y(), start.x() - (control or end).x())))
                for tip, angle in heads:
                    wing1 = QPointF(tip.x() - size * math.cos(angle - .45), tip.y() - size * math.sin(angle - .45))
                    wing2 = QPointF(tip.x() - size * math.cos(angle + .45), tip.y() - size * math.sin(angle + .45))
                    if style == "open":
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.drawPolyline(QPolygonF([wing1, tip, wing2]))
                    else:
                        painter.setBrush(color)
                        painter.drawPolygon(QPolygonF([tip, wing1, wing2]))
        elif kind in ("freehand", "polygon"):
            points = d.get("path", [])
            if points:
                path = QPainterPath(QPointF(*points[0]))
                for point in points[1:]:
                    path.lineTo(QPointF(*point))
                if kind == "polygon" and d.get("closed", True):
                    path.closeSubpath()
                painter.drawPath(path)
                if d.get("vertices"):                      # live preview: mark the corners already set
                    painter.setBrush(color)
                    for point in points[:d["vertices"]]:
                        painter.drawEllipse(QPointF(*point), 4, 4)
        elif kind == "bracket":
            mid = (y1 + y2) / 2
            path = QPainterPath(QPointF(x2, y1))
            path.cubicTo(x1, y1, x2, mid, x1, mid)
            path.cubicTo(x2, mid, x1, y2, x2, y2)
            painter.drawPath(path)
        elif kind == "spotlight":
            path = QPainterPath()
            path.addRect(self.boundingRect())
            path.addRoundedRect(rect, d.get("radius", 8), d.get("radius", 8))
            path.setFillRule(Qt.FillRule.OddEvenFill)
            painter.fillPath(path, QColor(0, 0, 0, d.get("dim", 160)))
        elif kind in ("stamp", "magnifier"):
            if kind == "stamp":
                data = base64.b64decode(d["image"])
                stamp = QImage.fromData(data, "PNG")
            else:
                box = self.sample_box()
                link = self.magnifier_link()
                if link is not None:
                    frame, lines = link
                    painter.save()
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(frame)
                    for start, end in lines:
                        painter.drawLine(start, end)
                    painter.restore()
                stamp = pil_to_qimage(self.view.source.crop(tuple(round(v) for v in box)))
                painter.save()
                clip = QPainterPath()
                clip.addEllipse(rect)
                painter.setClipPath(clip)
            painter.drawImage(rect, stamp)
            if kind == "magnifier":
                painter.restore()
                painter.drawEllipse(rect)
        elif kind == "highlight":
            color.setAlpha(85)
            painter.fillRect(rect, color)
        elif kind == "redact":
            painter.fillRect(rect, QColor("#111111"))
        elif kind in ("blur", "pixelate"):
            # Blur uses the source region; exporting flattens it into pixels.
            scene_rect = rect.translated(self.pos())
            bounds = scene_rect.toAlignedRect()
            crop_box = (bounds.left(), bounds.top(), bounds.right() + 1, bounds.bottom() + 1)
            param = max(1, d.get("blur", width * 4)) if kind == "blur" else max(2, width * 2)
            cache_key = (kind, id(self.view.source), crop_box, param)
            if getattr(self, "_effect_cache_key", None) != cache_key:
                crop = self.view.source.crop(crop_box)
                if crop.width and crop.height:
                    if kind == "blur":
                        crop = crop.filter(ImageFilter.GaussianBlur(param))
                    else:
                        crop = crop.resize((max(1, crop.width // param), max(1, crop.height // param)), Image.Resampling.BOX).resize(crop.size, Image.Resampling.NEAREST)
                    self._effect_cache_qimage = pil_to_qimage(crop)
                else:
                    self._effect_cache_qimage = None
                self._effect_cache_key = cache_key
            if getattr(self, "_effect_cache_qimage", None) is not None:
                painter.drawImage(rect, self._effect_cache_qimage)
        elif kind in ("text", "speech"):
            text_rect = self.body_rect().adjusted(8, 6, -8, -6)
            if kind == "speech":
                painter.setBrush(QColor(d.get("fill") or "#ffffff"))
                painter.drawRoundedRect(text_rect, 10, 10)
                painter.drawPolygon(QPolygonF([QPointF(text_rect.left() + 15, text_rect.bottom()),
                    QPointF(text_rect.left() + 35, text_rect.bottom()), self.tail_tip()]))
                text_rect = text_rect.adjusted(8, 6, -8, -6)
            font = base_font(d)
            alignment = {"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignHCenter,
                         "right": Qt.AlignmentFlag.AlignRight}[d.get("align", "left")]
            glow, shadow = d.get("glow_radius", 0), d.get("shadow_radius", 0)
            offset = d.get("shadow_offset", 0)
            if glow or shadow or offset:
                margin = math.ceil(max(glow, shadow) * 3 + abs(offset) + 2)
                area = text_rect.toAlignedRect().adjusted(-margin, -margin, margin, margin)
                mask = QImage(area.size(), QImage.Format.Format_RGBA8888)
                mask.fill(Qt.GlobalColor.transparent)
                ink = QPainter(mask)
                draw_text(ink, text_rect.translated(-area.x(), -area.y()), dict(d, text_color="white"), font, alignment)
                ink.end()
                alpha = qimage_to_pil(mask, keep_alpha=True).getchannel("A")
                for radius, shift, tone in ((shadow, offset, d.get("shadow_color", "#000000")), (glow, 0, d.get("glow_color", "#ffffff"))):
                    if radius or shift:
                        shade = Image.new("RGBA", alpha.size, QColor(tone).getRgb())
                        shade.putalpha(alpha.filter(ImageFilter.GaussianBlur(radius)))
                        painter.drawImage(QPointF(area.x() + shift, area.y() + shift), pil_to_qimage(shade))
            draw_text(painter, text_rect, d, font, alignment)
        elif kind == "step":
            circle = QRectF(x1, y1, max(4, d.get("text_width", 46) - 8), max(4, d.get("text_height", 46) - 8))
            prev = self.previous_step_center() if d.get("connect") else None
            if prev is not None:
                painter.drawLine(prev, circle.center())
            painter.setBrush(color)
            painter.drawEllipse(circle)
            painter.setPen(QColor("white"))
            font = QFont("Segoe UI")
            font.setPixelSize(max(6, round(21 * circle.height() / 38)))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, d["text"])
        if self.isSelected():
            painter.setPen(QPen(QColor("#2563eb"), 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            selection = self.body_rect().adjusted(-8, -8, 8, 8)
            painter.drawRect(selection)
            for handle in ([] if self.locked() else self.handles()):
                painter.fillRect(QRectF(handle - QPointF(4, 4), handle + QPointF(4, 4)), QColor("#2563eb"))
            if kind == "speech":
                painter.setBrush(QColor("#f59e0b"))
                painter.drawEllipse(self.tail_tip(), 5, 5)

    def mousePressEvent(self, event):
        self.original_pos = self.pos()
        self.text_only = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        self.ctrl_duplicate = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier and not self.text_only)
        if self.isSelected() and not self.locked():
            if self.data["kind"] == "speech" and (event.pos() - self.tail_tip()).manhattanLength() < 12:
                self.tail_before = self.serialize()
                self.ctrl_duplicate = False
                event.accept()
                return
            for index, handle in enumerate(self.handles()):
                if (event.pos() - handle).manhattanLength() < 14:
                    self.resize_before = self.serialize()
                    self.resize_handle = index
                    self.resize_press = event.pos()
                    self.resize_rect = self.body_rect()
                    self.ctrl_duplicate = False
                    event.accept()
                    return
        super().mousePressEvent(event)
        # Qt verschiebt beim Ziehen alle ausgewählten Markierungen mit; ihre Ausgangslagen für ein gemeinsames Undo.
        scene = self.scene()
        selected = scene.selectedItems() if scene is not None else []
        self.drag_origins = [(item, item.pos()) for item in selected if isinstance(item, Annotation) and item is not self]

    def mouseMoveEvent(self, event):
        if self.tail_before is not None:
            self.prepareGeometryChange()
            self.data["tail"] = [event.pos().x(), event.pos().y()]
            self.update()
            event.accept()
            return
        if self.resize_before is None:
            return super().mouseMoveEvent(event)
        self.prepareGeometryChange()
        old = self.resize_rect
        corners = [old.topLeft(), old.topRight(), old.bottomRight(), old.bottomLeft()]
        anchor = corners[(self.resize_handle + 2) % 4]
        corner = corners[self.resize_handle] + event.pos() - self.resize_press
        dx, dy = corner.x() - anchor.x(), corner.y() - anchor.y()
        sign_x = -1 if self.resize_handle in (0, 3) else 1
        sign_y = -1 if self.resize_handle in (0, 1) else 1
        width, height = max(4, dx * sign_x), max(4, dy * sign_y)
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier or self.data["kind"] == "stamp":
            factor = max(width / max(1, old.width()), height / max(1, old.height()))
            width, height = old.width() * factor, old.height() * factor
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            width = height = max(width, height)
        new = QRectF(anchor, anchor + QPointF(sign_x * width, sign_y * height)).normalized()
        self.data = resize_annotation(self.resize_before, old, new)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        if self.tail_before is not None:
            before, self.tail_before = self.tail_before, None
            self.view.undo.push(DataCommand(self, before, self.serialize()))
            event.accept()
            return
        if self.resize_before is not None:
            before, self.resize_before = self.resize_before, None
            self.view.undo.push(DataCommand(self, before, self.serialize()))
            event.accept()
            return
        super().mouseReleaseEvent(event)
        for item in self.view.annotations():
            if item.data["kind"] == "step" and item.data.get("connect"):
                item.prepareGeometryChange()
                item.update()
        others = [(item, before) for item, before in getattr(self, "drag_origins", []) if item.pos() != before]
        self.drag_origins = []
        if self.pos() != self.original_pos:
            special = (getattr(self, "ctrl_duplicate", False) or (getattr(self, "text_only", False)
                       and self.data["kind"] in ("speech", "magnifier")))
            if others and not special:
                items = [(self, self.original_pos)] + others
                self.view.undo.push(GroupMoveCommand([(item, before, item.pos()) for item, before in items]))
                return
            for item, before in others:          # Duplizieren/Alt-Ziehen betrifft nur die gegriffene Markierung
                item.setPos(before)
            if getattr(self, "ctrl_duplicate", False):
                self.duplicate_to(self.original_pos, self.pos())
            elif self.data["kind"] == "speech" and getattr(self, "text_only", False):
                self.move_text_only(self.original_pos, self.pos())
            elif self.data["kind"] == "magnifier" and getattr(self, "text_only", False):
                self.move_magnifier_glass_only(self.original_pos, self.pos())
            else:
                self.view.undo.push(MoveCommand(self, self.original_pos, self.pos()))

    def duplicate_to(self, start, end):
        """Ctrl-drag on an item: keep the original at start and add a copy at end (one undo step)."""
        self.setPos(start)
        copy_data = dict(self.serialize(), pos=[end.x(), end.y()])
        if copy_data.get("kind") == "step":
            number = 1 + sum(x.data["kind"] == "step" for x in self.view.annotations())
            copy_data["number"] = number
            copy_data["text"] = step_label(number, copy_data.get("step_format", "1"))
        item = self.view.add_annotation(copy_data)
        self.scene().clearSelection()
        item.setSelected(True)

    def move_magnifier_glass_only(self, start, end):
        """Alt-drag on a magnifier: move the glass while keeping the sampled source area fixed."""
        rect, zoom = self.body_rect(), max(1, self.data.get("zoom", 2))
        center = rect.center() + start
        sample_box = list(self.data.get("sample_box", [
            center.x() - rect.width() / zoom / 2, center.y() - rect.height() / zoom / 2,
            center.x() + rect.width() / zoom / 2, center.y() + rect.height() / zoom / 2]))
        self.view.undo.beginMacro(tr("Lupenglas verschieben"))
        self.view.undo.push(MoveCommand(self, start, end))
        before = self.serialize()
        self.view.undo.push(DataCommand(self, before, dict(before, sample_box=sample_box)))
        self.view.undo.endMacro()

    def move_text_only(self, start, end):
        """Alt-drag on a speech bubble: the text box moves, the pointer tip stays where it was (one undo step)."""
        tail = self.tail_tip() - (end - start)
        self.view.undo.beginMacro(tr("Text verschieben"))
        self.view.undo.push(MoveCommand(self, start, end))
        before = self.serialize()
        self.view.undo.push(DataCommand(self, before, dict(before, tail=[tail.x(), tail.y()])))
        self.view.undo.endMacro()

    def mouseDoubleClickEvent(self, event):
        if self.data["kind"] in ("text", "speech"):
            dialog = RichTextDialog(self.view, self.data)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                text, spans = dialog.result_values()
                before = self.serialize()
                after = {key: value for key, value in dict(before, text=text, spans=spans).items() if key != "spans" or spans}
                self.view.undo.push(DataCommand(self, before, after))
        elif self.data["kind"] == "step":
            text, ok = QInputDialog.getMultiLineText(self.view, tr("Text ändern"), tr("Beschriftung"), self.data["text"])
            if ok:
                before = dict(self.data)
                after = dict(before, text=text)
                self.view.undo.push(DataCommand(self, before, after))
        else:
            super().mouseDoubleClickEvent(event)

    def serialize(self):
        return dict(self.data, pos=[self.pos().x(), self.pos().y()], rotation=self.rotation())


class MoveCommand(QUndoCommand):
    def __init__(self, item, before, after):
        super().__init__(tr("Markierung verschieben"))
        self.item, self.before, self.after = item, before, after

    def redo(self):
        self.item.setPos(self.after)

    def undo(self):
        self.item.setPos(self.before)


class GroupMoveCommand(QUndoCommand):
    """Mehrere gemeinsam gezogene Markierungen in einem Undo-Schritt; `moves` = [(Markierung, vorher, nachher)]."""

    def __init__(self, moves):
        super().__init__(tr("Markierungen verschieben"))
        self.moves = [(item, QPointF(before), QPointF(after)) for item, before, after in moves]

    def redo(self):
        for item, _before, after in self.moves:
            item.setPos(after)

    def undo(self):
        for item, before, _after in self.moves:
            item.setPos(before)


class NudgeCommand(MoveCommand):
    """Pfeiltasten: Markierungen um wenige Pixel verschieben; direkt folgende Tastendrücke
    auf dieselbe Auswahl werden zu einem Undo-Schritt zusammengefasst."""
    ID = 4711

    def __init__(self, items, delta):
        super().__init__(items[0], items[0].pos(), items[0].pos() + delta)
        self.setText(tr("Markierung mit Pfeiltasten verschieben"))
        self.items = list(items)
        self.befores = [item.pos() for item in self.items]
        self.delta = QPointF(delta)

    def id(self):
        return self.ID

    def mergeWith(self, other):
        if not isinstance(other, NudgeCommand) or [id(x) for x in other.items] != [id(x) for x in self.items]:
            return False
        self.delta += other.delta
        return True

    def redo(self):
        for item, before in zip(self.items, self.befores):
            item.setPos(before + self.delta)

    def undo(self):
        for item, before in zip(self.items, self.befores):
            item.setPos(before)


class DataCommand(QUndoCommand):
    def __init__(self, item, before, after):
        super().__init__(tr("Text ändern"))
        self.item, self.before, self.after = item, before, after

    def apply(self, value):
        self.item.prepareGeometryChange()
        self.item.data = dict(value)
        self.item.setPos(*value.get("pos", [0, 0]))
        self.item.setTransformOriginPoint(self.item.body_rect().center())
        self.item.setRotation(value.get("rotation", 0))
        self.item.apply_lock()
        self.item.update()

    def redo(self):
        self.apply(self.after)

    def undo(self):
        self.apply(self.before)
