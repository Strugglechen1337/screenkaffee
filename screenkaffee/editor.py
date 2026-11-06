"""Re-editable annotations over an immutable source image."""
import logging
import json
import math
import base64
import io
from pathlib import Path
from PIL import Image, ImageFilter
from PySide6.QtCore import Qt, QPointF, QRectF, Signal, Slot, QThread, QTimer, QSize
from PySide6.QtGui import (QColor, QImage, QPainter, QPen, QBrush, QFont,
    QPixmap, QPolygonF, QTransform, QKeySequence, QShortcut, QUndoCommand, QUndoStack, QPainterPath, QAction)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGraphicsScene, QGraphicsView, QGraphicsItem, QToolBar, QColorDialog,
    QInputDialog, QLabel, QSpinBox, QComboBox, QFileDialog, QMessageBox,
    QDialog, QDialogButtonBox, QSizePolicy, QFormLayout, QCheckBox, QDoubleSpinBox, QLineEdit, QMenu, QToolButton)
from .color import save_png, save_image, srgb_profile
from .richtext import RichTextDialog, base_font, draw_text


LOG = logging.getLogger(__name__)
from .editor_items import *  # noqa: F401,F403  (Zeichenobjekte, früher hier)
from .editor_commands import *  # noqa: F401,F403  (Undo-Befehle, früher hier)


class EditorView(QGraphicsView):
    colorPicked = Signal(str)
    measured = Signal(str)
    zoomChanged = Signal(float)
    toolFinished = Signal()
    toolChanged = Signal(str)
    styleChanged = Signal()

    def __init__(self, image):
        super().__init__()
        self.source = image.convert("RGBA" if "A" in image.getbands() else "RGB")
        self.source_changed = False
        scene = QGraphicsScene(self)
        self.setScene(scene)
        self.base = scene.addPixmap(QPixmap.fromImage(pil_to_qimage(image)))
        self.base.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.base.setZValue(-100)
        self.crop_rect = QRectF(0, 0, image.width, image.height)
        scene.setSceneRect(self.crop_rect)
        self.undo = QUndoStack(self)
        self.tool, self.color, self.width, self.font_size = "select", "#ff5a36", 4, 22
        from .presets import load_defaults
        self.tool_defaults = load_defaults()
        from .effects import ImageEffects
        self.effects = ImageEffects().to_dict()
        self.effects["transparent_background"] = "A" in self.source.getbands() and self.source.getchannel("A").getextrema()[0] < 255
        self.path_points = []
        self.polygon_points = []
        self.magnet = True
        self.start = None
        self.preview = None
        self.crop_handle = None
        self.measure_line = None          # Lineal: [Start, Ende] in Bildkoordinaten, wird nicht gespeichert
        self.measuring = False
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#202020"))
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setMouseTracking(True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.shortcut_delete = QShortcut(QKeySequence(Qt.Key.Key_Delete), self)
        self.shortcut_delete.activated.connect(self.delete_selected)
        self.setAcceptDrops(True)
        self.copy_shortcut = QShortcut(QKeySequence("Ctrl+C"), self)
        self.copy_shortcut.activated.connect(self.copy_annotations)
        self.paste_shortcut = QShortcut(QKeySequence("Ctrl+V"), self)
        self.paste_shortcut.activated.connect(self.paste_annotations)
        dpr = self._screen_dpr()
        if dpr != 1.0:
            self.scale(1.0 / dpr, 1.0 / dpr)

    def _screen_dpr(self):
        import os
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return 1.0
        win = self.window()
        screen = (win.screen() if win and win.screen() else None) or QApplication.primaryScreen()
        return float(screen.devicePixelRatio()) if screen else 1.0

    def set_tool(self, tool):
        self.cancel_drawing()
        self.polygon_points = []
        self.remember_tool_style()
        self.measure_line, self.measuring = None, False
        self.tool = tool
        style = self.tool_defaults.get(tool, {})
        self.color, self.width, self.font_size = style.get("color", self.color), style.get("width", self.width), style.get("font_size", self.font_size)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag if tool == "select" else QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.ArrowCursor if tool == "select" else Qt.CursorShape.CrossCursor)
        self.viewport().update()
        self.styleChanged.emit()
        self.toolChanged.emit(tool)

    def remember_tool_style(self):
        self.tool_defaults.setdefault(self.tool, {}).update(color=self.color, width=self.width, font_size=self.font_size)

    def set_crop(self, rect):
        self.crop_rect = QRectF(rect)
        self.scene().setSceneRect(rect)
        self.fit_to_crop()
        self.viewport().update()

    def _crop_handles(self, r=None):
        r = r or self.crop_rect
        cx, cy = r.center().x(), r.center().y()
        return {
            "tl": QPointF(r.left(), r.top()),
            "tc": QPointF(cx, r.top()),
            "tr": QPointF(r.right(), r.top()),
            "ml": QPointF(r.left(), cy),
            "mr": QPointF(r.right(), cy),
            "bl": QPointF(r.left(), r.bottom()),
            "bc": QPointF(cx, r.bottom()),
            "br": QPointF(r.right(), r.bottom()),
        }

    def _crop_handle_at(self, pos):
        scale = max(0.05, self.view_scale())
        tol = 10.0 / scale
        for name, pt in self._crop_handles().items():
            if math.hypot(pos.x() - pt.x(), pos.y() - pt.y()) <= tol:
                return name
        return None

    def drawForeground(self, painter, rect):
        """Everything outside the crop is hidden on screen (it stays in the original and returns with Undo)."""
        super().drawForeground(painter, rect)
        if getattr(self, "_rendering_output", False):
            return
        outside = QPainterPath()
        outside.addRect(rect)
        inside = QPainterPath()
        inside.addRect(self.crop_rect)
        painter.fillPath(outside.subtracted(inside), QColor("#202020"))
        border = int(self.effects.get("border_width", 0) or 0)
        if border:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(self.effects.get("border_color", "#1e293b")), min(border, 100)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            half = border / 2
            radius = max(0, int(self.effects.get("corner_radius", 0) or 0))
            painter.drawRoundedRect(self.crop_rect.adjusted(half, half, -half, -half), radius, radius)
            painter.restore()
        if self.tool == "crop":
            scale = max(0.05, self.view_scale())
            active = self.preview.rect() if self.preview is not None else self.crop_rect
            if self.preview is not None and active != self.crop_rect:
                dim = QPainterPath()
                dim.addRect(self.crop_rect)
                keep = QPainterPath()
                keep.addRect(active)
                painter.fillPath(dim.subtracted(keep), QColor(0, 0, 0, 120))
            painter.save()
            painter.setPen(QPen(QColor("#ffffff"), 1.5 / scale))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(active)
            pen = QPen(QColor("#2563eb"), 1.8 / scale, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(active)
            hs = 5.0 / scale
            painter.setPen(QPen(QColor("#ffffff"), 1.2 / scale))
            painter.setBrush(QColor("#2563eb"))
            for pt in self._crop_handles(active).values():
                painter.drawRect(QRectF(pt.x() - hs, pt.y() - hs, hs * 2, hs * 2))
            if self.preview is not None:
                badge_w, badge_h = 110.0 / scale, 22.0 / scale
                bx = max(self.crop_rect.left(), min(active.left(), self.crop_rect.right() - badge_w))
                by = active.top() - badge_h - 4.0 / scale if active.top() - badge_h - 4.0 / scale >= self.crop_rect.top() else active.top() + 4.0 / scale
                badge_rect = QRectF(bx, by, badge_w, badge_h)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(24, 24, 27, 220))
                painter.drawRoundedRect(badge_rect, 4.0 / scale, 4.0 / scale)
                painter.setPen(QColor("#ffffff"))
                font = QFont("Segoe UI")
                font.setPixelSize(max(1, round(11.0 / scale)))
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, f"{round(active.width())} × {round(active.height())} px")
            painter.restore()
        elif self.tool == "cutout" and self.preview is not None:
            scale = max(0.05, self.view_scale())
            strip = self.preview.rect()
            axis = getattr(self, "_cutout_axis", "y")
            span = strip.height() if axis == "y" else strip.width()
            painter.save()
            painter.fillRect(strip, QColor(220, 38, 38, 105))
            painter.setPen(QPen(QColor("#ef4444"), 2.0 / scale, Qt.PenStyle.DashLine))
            if axis == "y":
                painter.drawLine(QPointF(strip.left(), strip.top()), QPointF(strip.right(), strip.top()))
                painter.drawLine(QPointF(strip.left(), strip.bottom()), QPointF(strip.right(), strip.bottom()))
            else:
                painter.drawLine(QPointF(strip.left(), strip.top()), QPointF(strip.left(), strip.bottom()))
                painter.drawLine(QPointF(strip.right(), strip.top()), QPointF(strip.right(), strip.bottom()))
            badge_w, badge_h = 175.0 / scale, 22.0 / scale
            badge_rect = QRectF(strip.center().x() - badge_w / 2, strip.center().y() - badge_h / 2, badge_w, badge_h)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(24, 24, 27, 225))
            painter.drawRoundedRect(badge_rect, 4.0 / scale, 4.0 / scale)
            painter.setPen(QColor("#ffffff"))
            font = QFont("Segoe UI")
            font.setPixelSize(max(1, round(11.0 / scale)))
            font.setBold(True)
            painter.setFont(font)
            label = "Waagerecht" if axis == "y" else "Senkrecht"
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, f"{label} entfernen: {round(span)} px")
            painter.restore()

        if self.tool == "measure" and self.measure_line:
            from . import ruler
            start, end = self.measure_line
            ruler.paint(painter, start, end, ruler.measurement(start.x(), start.y(), end.x(), end.y()),
                        pixel_scale=1 / max(.05, self.view_scale()), guides=QRectF(self.crop_rect))

    def measure_point(self, event):
        pos = self.mapToScene(event.position().toPoint())
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and self.measure_line:
            from .ruler import snap_axis
            start = self.measure_line[0]
            pos = QPointF(*snap_axis(start.x(), start.y(), pos.x(), pos.y()))
        return pos

    def measurement(self):
        if not self.measure_line:
            return None
        from .ruler import measurement
        start, end = self.measure_line
        return measurement(start.x(), start.y(), end.x(), end.y())

    def add_annotation(self, data):
        item = Annotation(data, self)
        item.setZValue(max((x.zValue() for x in self.annotations()), default=0) + 1)
        self.undo.push(AddCommand(self, item))
        return item

    def annotations(self):
        return sorted((x for x in self.scene().items() if isinstance(x, Annotation) and x is not self.preview), key=lambda x: x.zValue())

    def cancel_drawing(self):
        if self.preview is not None:
            self.scene().removeItem(self.preview)
        self.preview = None
        self.start = None
        self.path_points = []
        self.viewport().update()

    SQUARE_TOOLS = ("rectangle", "ellipse", "highlight", "spotlight", "blur", "pixelate", "redact", "magnifier", "bracket")

    def snap_position(self, item, new_pos, threshold=6):
        """Magnet: align a dragged item's edges/centre with other items and the canvas within a few pixels."""
        rect = item.body_rect().translated(new_pos)
        limit = threshold / max(.05, self.view_scale())
        others = [QRectF(self.crop_rect)] + [o.body_rect().translated(o.pos()) for o in self.annotations() if o is not item]
        best = {}
        for axis, mine in (("x", (rect.left(), rect.center().x(), rect.right())), ("y", (rect.top(), rect.center().y(), rect.bottom()))):
            for other in others:
                targets = (other.left(), other.center().x(), other.right()) if axis == "x" else (other.top(), other.center().y(), other.bottom())
                for value in mine:
                    for target in targets:
                        delta = target - value
                        if abs(delta) <= limit and (axis not in best or abs(delta) < abs(best[axis])):
                            best[axis] = delta
        return QPointF(new_pos.x() + best.get("x", 0), new_pos.y() + best.get("y", 0))

    def color_under(self, pos):
        x = max(0, min(self.source.width - 1, int(pos.x())))
        y = max(0, min(self.source.height - 1, int(pos.y())))
        return "#%02x%02x%02x" % self.source.convert("RGB").getpixel((x, y))

    def drawing_data(self, end, square=None):
        cover = getattr(self, "cover_background", False) and self.tool == "rectangle"
        if square is None:
            square = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier) and not cover
        if square and self.tool in self.SQUARE_TOOLS:
            dx, dy = end.x() - self.start.x(), end.y() - self.start.y()
            side = max(abs(dx), abs(dy))
            end = QPointF(self.start.x() + math.copysign(side, dx or 1), self.start.y() + math.copysign(side, dy or 1))
        data = dict(self.tool_defaults.get(self.tool, {}))
        data.update({"kind": self.tool, "color": self.color, "width": self.width,
                "points": [self.start.x(), self.start.y(), end.x(), end.y()]})
        if cover:
            data["fill"] = self.color
        if self.tool == "freehand":
            data["path"] = self.path_points + [[end.x(), end.y()]]
            xs, ys = zip(*data["path"])
            data["points"] = [min(xs), min(ys), max(xs), max(ys)]
        return data

    def _crop_rect_from_handle(self, handle, pos):
        r = QRectF(self.crop_rect)
        x = max(r.left(), min(r.right(), pos.x()))
        y = max(r.top(), min(r.bottom(), pos.y()))
        left, top, right, bottom = r.left(), r.top(), r.right(), r.bottom()
        if "l" in handle:
            left = min(x, right - 2)
        if "r" in handle:
            right = max(x, left + 2)
        if "t" in handle:
            top = min(y, bottom - 2)
        if "b" in handle:
            bottom = max(y, top + 2)
        rect = QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier:
            side = min(rect.width(), rect.height())
            rect.setWidth(side)
            rect.setHeight(side)
        return rect

    def _constrain_crop_end(self, start, end):
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier:
            dx, dy = end.x() - start.x(), end.y() - start.y()
            side = max(abs(dx), abs(dy))
            return QPointF(start.x() + math.copysign(side, dx or 1), start.y() + math.copysign(side, dy or 1))
        return end

    def _cutout_strip(self, start, end):
        dx, dy = abs(end.x() - start.x()), abs(end.y() - start.y())
        axis = "x" if dx > dy else "y"
        self._cutout_axis = axis
        if axis == "y":
            y1 = max(0, min(self.source.height, min(start.y(), end.y())))
            y2 = max(0, min(self.source.height, max(start.y(), end.y())))
            return axis, round(y1), round(y2), QRectF(0, y1, self.source.width, max(1, y2 - y1))
        x1 = max(0, min(self.source.width, min(start.x(), end.x())))
        x2 = max(0, min(self.source.width, max(start.x(), end.x())))
        return axis, round(x1), round(x2), QRectF(x1, 0, max(1, x2 - x1), self.source.height)

    def update_drawing(self, pos):
        if self.tool == "crop":
            if self.crop_handle:
                self.preview.setRect(self._crop_rect_from_handle(self.crop_handle, pos))
            else:
                adj = self._constrain_crop_end(self.start, pos)
                self.preview.setRect(QRectF(self.start, adj).normalized().intersected(self.crop_rect))
            self.viewport().update()
            return
        if self.tool == "cutout":
            _, _, _, strip = self._cutout_strip(self.start, pos)
            self.preview.setRect(strip)
            self.viewport().update()
            return
        pos = QPointF(max(self.crop_rect.left(), min(self.crop_rect.right(), pos.x())),
                      max(self.crop_rect.top(), min(self.crop_rect.bottom(), pos.y())))
        self.preview.prepareGeometryChange()
        self.preview.data = self.drawing_data(pos)
        self.preview.update()
        self.viewport().update()

    def delete_selected(self):
        items = [x for x in self.scene().selectedItems() if isinstance(x, Annotation)]
        if items:
            self.undo.push(DeleteCommand(self, items))

    def scale_selection(self, factor):
        if not math.isfinite(factor) or not .05 <= factor <= 20:
            raise ValueError("Skalierung muss zwischen 5 und 2000 Prozent liegen.")
        items = [x for x in self.annotations() if x.isSelected()]
        if not items:
            return
        bounds = items[0].sceneBoundingRect()
        for item in items[1:]:
            bounds = bounds.united(item.sceneBoundingRect())
        anchor = bounds.topLeft()
        self.undo.beginMacro("Auswahl skalieren")
        for item in items:
            before = item.serialize()
            old = item.body_rect()
            new = QRectF(old.x() * factor, old.y() * factor, old.width() * factor, old.height() * factor)
            after = resize_annotation(before, old, new)
            after["pos"] = [anchor.x() + (item.pos().x() - anchor.x()) * factor,
                            anchor.y() + (item.pos().y() - anchor.y()) * factor]
            for key in ("width", "font_size", "radius", "shadow_radius", "shadow_offset", "glow_radius"):
                if key in after:
                    after[key] = after[key] * factor
            self.undo.push(DataCommand(item, before, after))
        self.undo.endMacro()

    def toggle_magnifier_lock(self):
        items = [x for x in self.annotations() if x.isSelected() and x.data["kind"] == "magnifier"]
        if not items:
            return
        self.undo.beginMacro("Lupenquelle sperren / lösen")
        for item in items:
            before = item.serialize()
            after = dict(before)
            if "sample_box" in after:
                after.pop("sample_box")
            else:
                rect, zoom = item.body_rect(), max(1, item.data.get("zoom", 2))
                center = rect.center() + item.pos()
                after["sample_box"] = [center.x() - rect.width() / zoom / 2, center.y() - rect.height() / zoom / 2,
                    center.x() + rect.width() / zoom / 2, center.y() + rect.height() / zoom / 2]
            self.undo.push(DataCommand(item, before, after))
        self.undo.endMacro()

    def copy_annotations(self):
        from PySide6.QtCore import QMimeData
        from PySide6.QtWidgets import QApplication
        items = [x.serialize() for x in self.scene().selectedItems() if isinstance(x, Annotation)]
        if items:
            mime = QMimeData()
            mime.setData("application/x-prism-annotations", json.dumps(items).encode())
            QApplication.clipboard().setMimeData(mime)

    def paste_annotations(self):
        from PySide6.QtWidgets import QApplication
        mime = QApplication.clipboard().mimeData()
        if mime.hasFormat("application/x-prism-annotations"):
            self.undo.beginMacro("Markierungen einfügen")
            for data in json.loads(bytes(mime.data("application/x-prism-annotations"))):
                pos = data.get("pos", [0, 0])
                data["pos"] = [pos[0] + 15, pos[1] + 15]
                self.add_annotation(data)
            self.undo.endMacro()
        elif mime.hasImage():
            self.insert_stamp(qimage_to_pil(QApplication.clipboard().image(), keep_alpha=True))

    def insert_stamp(self, image, pos=None):
        image.thumbnail((self.source.width, self.source.height))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        pos = pos or self.crop_rect.center()
        self.add_annotation({"kind": "stamp", "color": self.color, "width": self.width,
            "points": [pos.x(), pos.y(), pos.x() + image.width, pos.y() + image.height],
            "original_size": [image.width, image.height],
            "image": base64.b64encode(buffer.getvalue()).decode()})

    def restore_stamp_size(self):
        """Restore selected stamps to their original pixel dimensions."""
        items = [x for x in self.annotations() if x.isSelected() and x.data["kind"] == "stamp"]
        if not items:
            return False
        self.undo.beginMacro("Originalgröße wiederherstellen")
        for item in items:
            before = item.serialize()
            size = before.get("original_size")
            if not size:
                stamp = QImage.fromData(base64.b64decode(before["image"]), "PNG")
                size = [stamp.width(), stamp.height()]
            x1, y1 = before["points"][:2]
            after = dict(before, points=[x1, y1, x1 + size[0], y1 + size[1]])
            self.undo.push(DataCommand(item, before, after))
        self.undo.endMacro()
        return True

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        from .color import import_srgb
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.insert_stamp(import_srgb(url.toLocalFile()), self.mapToScene(event.position().toPoint()))
        event.acceptProposedAction()

    def mousePressEvent(self, event):
        if self.tool == "polygon" and event.button() == Qt.MouseButton.RightButton:
            self.finish_polygon(bool(event.modifiers() & Qt.KeyboardModifier.AltModifier))
            return
        if self.tool != "select" and event.button() == Qt.MouseButton.RightButton:
            self.set_tool("select")
            self.toolFinished.emit()
            event.accept()
            return
        if self.tool == "select" or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        pos = self.mapToScene(event.position().toPoint())
        if self.tool not in ("crop", "cutout") and not self.crop_rect.contains(pos):
            return
        if self.tool == "polygon":
            self.polygon_points.append([pos.x(), pos.y()])
            self.update_polygon_preview(pos)
            return
        if self.tool == "measure":
            self.measure_line = [pos, QPointF(pos)]
            self.measuring = True
            self.viewport().update()
            return
        if self.tool == "picker":
            x, y = int(pos.x()), int(pos.y())
            rgb = self.source.convert("RGB").getpixel((x, y))
            value = "#%02X%02X%02X" % rgb
            self.colorPicked.emit(value)
            return
        if self.tool in ("text", "step", "speech"):
            if self.tool in ("text", "speech"):
                text, ok = QInputDialog.getMultiLineText(self, "Text", "Beschriftung")
                if not ok or not text:
                    return
            else:
                number = 1 + sum(x.data["kind"] == "step" for x in self.annotations())
                text = step_label(number, self.tool_defaults.get("step", {}).get("step_format", "1"))
            view_rot = int(self.effects.get("rotation", 0) or 0)
            rot_entry = {"rotation": -view_rot} if view_rot else {}
            self.add_annotation({**self.tool_defaults.get(self.tool, {}), **rot_entry, **({"number": number} if self.tool == "step" else {}),
                "kind": self.tool, "color": self.color, "width": self.width,
                "points": [pos.x(), pos.y(), pos.x() + 300, pos.y() + 100],
                "text": text, "text_width": 320 if self.tool != "step" else 46,
                "text_height": max(100, len(text.splitlines()) * self.font_size * 2) if self.tool != "step" else 46,
                "font_size": self.font_size})
            return
        self.cover_background = bool(self.tool == "rectangle" and (event.modifiers() & Qt.KeyboardModifier.AltModifier)
                                     and (event.modifiers() & Qt.KeyboardModifier.ControlModifier))
        if event.modifiers() & Qt.KeyboardModifier.AltModifier and self.tool not in ("crop", "cutout"):
            # Alt: draw with the colour found under the mouse (e.g. to cover something with its own background).
            self.color = self.color_under(pos)
            self.styleChanged.emit()
        self.scene().clearSelection()
        self.start = pos
        self.path_points = [[pos.x(), pos.y()]]
        if self.tool == "crop":
            self.crop_handle = self._crop_handle_at(pos)
            initial = QRectF(self.crop_rect) if self.crop_handle else QRectF(pos, pos)
            self.preview = self.scene().addRect(initial, QPen(QColor(self.color), 2, Qt.PenStyle.DashLine))
        elif self.tool == "cutout":
            self.crop_handle = None
            _, _, _, strip = self._cutout_strip(pos, pos)
            self.preview = self.scene().addRect(strip, QPen(Qt.PenStyle.NoPen))
        else:
            self.crop_handle = None
            self.preview = Annotation(self.drawing_data(pos), self)
            self.preview.setFlags(QGraphicsItem.GraphicsItemFlag(0))
            self.preview.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene().addItem(self.preview)
        self.preview.setZValue(100000)

    def mouseMoveEvent(self, event):
        if self.tool == "measure" and self.measuring and self.measure_line:
            self.measure_line[1] = self.measure_point(event)
            self.viewport().update()
            return
        if self.start is not None:
            pos = self.mapToScene(event.position().toPoint())
            if self.tool == "freehand":
                self.path_points.append([pos.x(), pos.y()])
            self.update_drawing(pos)
            return
        if self.tool == "polygon" and self.polygon_points:
            self.update_polygon_preview(self.mapToScene(event.position().toPoint()))
            return
        if self.tool == "crop":
            pos = self.mapToScene(event.position().toPoint())
            h = self._crop_handle_at(pos)
            self.setCursor(self.crop_handle_cursor(h))
        super().mouseMoveEvent(event)

    HANDLE_DIRECTIONS = {"tl": (-1, -1), "tc": (0, -1), "tr": (1, -1), "ml": (-1, 0),
                         "mr": (1, 0), "bl": (-1, 1), "bc": (0, 1), "br": (1, 1)}

    def crop_handle_cursor(self, handle):
        """Resize cursor for a crop handle, turned with the view (rotation/flip)."""
        if handle not in self.HANDLE_DIRECTIONS:
            return Qt.CursorShape.CrossCursor
        dx, dy = self.HANDLE_DIRECTIONS[handle]
        t = self.transform()
        sx, sy = dx * t.m11() + dy * t.m21(), dx * t.m12() + dy * t.m22()
        angle = round((math.degrees(math.atan2(sy, sx)) % 180) / 45) % 4
        return (Qt.CursorShape.SizeHorCursor, Qt.CursorShape.SizeFDiagCursor,
                Qt.CursorShape.SizeVerCursor, Qt.CursorShape.SizeBDiagCursor)[angle]

    def mouseReleaseEvent(self, event):
        if self.tool == "measure" and self.measuring and event.button() == Qt.MouseButton.LeftButton:
            from .ruler import label
            self.measure_line[1] = self.measure_point(event)
            self.measuring = False
            self.viewport().update()
            self.measured.emit(label(self.measurement()))
            return
        if self.start is None or event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        raw_end = self.mapToScene(event.position().toPoint())
        end = QPointF(max(self.crop_rect.left(), min(self.crop_rect.right(), raw_end.x())),
                      max(self.crop_rect.top(), min(self.crop_rect.bottom(), raw_end.y())))
        start = self.start
        data = self.drawing_data(end)
        path = data.get("path", [])
        self.start = None
        preview_rect = self.preview.rect() if self.tool == "crop" and self.preview is not None else None
        handle, self.crop_handle = self.crop_handle, None
        self.scene().removeItem(self.preview)
        self.preview = None
        self.viewport().update()
        if self.tool == "crop":
            target = preview_rect if handle else QRectF(start, self._constrain_crop_end(start, raw_end)).normalized()
            # Snap both edges independently; enclosing fractional mouse coordinates
            # would otherwise add pixels after viewport-to-scene conversion.
            rect = QRectF(QPointF(round(target.left()), round(target.top())),
                          QPointF(round(target.right()), round(target.bottom()))).intersected(self.crop_rect)
            if rect.width() >= 2 and rect.height() >= 2 and rect != self.crop_rect:
                self.undo.push(CropCommand(self, rect))
                self.set_tool("select")
                self.toolFinished.emit()
            return
        if self.tool == "cutout":
            from .effects import cut_out
            axis, s, e, _ = self._cutout_strip(start, raw_end)
            limit = self.source.height if axis == "y" else self.source.width
            if e - s >= 2 and (e - s) < limit:
                shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                gap = getattr(self, "cutout_gap", 6 if shift else 0)
                torn = getattr(self, "cutout_torn", 6 if shift else 0)
                try:
                    image = cut_out(self.source, axis, s, e, gap=gap, torn=torn)
                    self.undo.push(CutOutCommand(self, image, axis, s, e))
                except ValueError:
                    pass
                self.set_tool("select")
                self.toolFinished.emit()
            return
        rect = QRectF(start, end).normalized()
        if rect.width() < 2 and rect.height() < 2 and not (path and (data["points"][2] - data["points"][0] >= 2 or data["points"][3] - data["points"][1] >= 2)):
            return
        self.add_annotation(data)

    def update_polygon_preview(self, cursor=None):
        """Live view while clicking points: the segments so far, a rubber band to the mouse and a dot per corner."""
        if not self.polygon_points:
            self.cancel_drawing()
            return
        path = [list(point) for point in self.polygon_points]
        if cursor is not None:
            path.append([max(self.crop_rect.left(), min(self.crop_rect.right(), cursor.x())),
                         max(self.crop_rect.top(), min(self.crop_rect.bottom(), cursor.y()))])
        xs, ys = zip(*path)
        data = {**self.tool_defaults.get("polygon", {}), "kind": "polygon", "color": self.color, "width": self.width,
                "points": [min(xs), min(ys), max(xs), max(ys)], "path": path, "closed": False, "vertices": len(self.polygon_points)}
        if self.preview is None:
            self.preview = Annotation(data, self)
            self.preview.setFlags(QGraphicsItem.GraphicsItemFlag(0))
            self.preview.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.preview.setZValue(100000)
            self.scene().addItem(self.preview)
        else:
            self.preview.prepareGeometryChange()
            self.preview.data = data
            self.preview.update()
        self.viewport().update()
        window = self.window()
        if hasattr(window, "statusBar"):
            window.statusBar().showMessage("Polygon: Klicken setzt Punkte · Doppelklick, Rechtsklick oder Enter beendet und schließt die Figur · Alt beim Beenden lässt sie offen · Esc bricht ab")

    def finish_polygon(self, keep_open=False):
        """A polygon is closed when finished; with Alt held (keep_open=True) it stays an open line of segments."""
        self.cancel_drawing()
        if len(self.polygon_points) >= (2 if keep_open else 3):
            xs, ys = zip(*self.polygon_points)
            self.add_annotation({"kind": "polygon", "color": self.color, "width": self.width,
                "points": [min(xs), min(ys), max(xs), max(ys)], "path": list(self.polygon_points), "closed": not keep_open})
        self.polygon_points = []

    def mouseDoubleClickEvent(self, event):
        if self.tool == "polygon":
            self.finish_polygon(bool(event.modifiers() & Qt.KeyboardModifier.AltModifier))
        else:
            super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.set_tool("select")
            self.toolFinished.emit()
            return
        if self.tool == "polygon" and self.polygon_points and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish_polygon(bool(event.modifiers() & Qt.KeyboardModifier.AltModifier))
            return
        super().keyPressEvent(event)

    def view_scale(self):
        t = self.transform()
        return math.hypot(t.m11(), t.m12())

    def zoom_percent(self):
        return self.view_scale() * self._screen_dpr() * 100

    def _flip_factors(self):
        return (-1.0 if self.effects.get("flip_horizontal") else 1.0, -1.0 if self.effects.get("flip_vertical") else 1.0)

    def apply_view_transform(self, scale=None):
        """Set zoom and show rotation/flip (Effects) live in the view; order matches apply_effects (flip, then rotate)."""
        scale = self.view_scale() if scale is None else scale
        fx, fy = self._flip_factors()
        center = self.mapToScene(self.viewport().rect().center())
        self.resetTransform()
        self.rotate(float(self.effects.get("rotation", 0)))
        self.scale(scale * fx, scale * fy)
        self.centerOn(center)

    def refresh_effects_preview(self, refit=True):
        """Show the non-destructive image effects live: rotation, flips, gray/brightness/contrast (border is drawn in the foreground)."""
        self._update_base_pixmap(adjusted=True)
        if refit:
            self.fit_to_crop()
        else:
            self.apply_view_transform()
        self.viewport().update()

    def _update_base_pixmap(self, adjusted):
        from .effects import adjust_colors, ImageEffects
        e = ImageEffects.from_dict(self.effects)
        tinted = e.grayscale or e.brightness != 1 or e.contrast != 1
        wanted = "adjusted" if adjusted and tinted else "plain"
        key = (wanted, id(self.source), e.grayscale, e.brightness, e.contrast)
        if getattr(self, "_base_key", None) == key:
            return
        self._base_key = key
        image = adjust_colors(self.source, e) if wanted == "adjusted" else self.source
        self.base.setPixmap(QPixmap.fromImage(pil_to_qimage(image)))

    def fit_to_crop(self):
        fx, fy = self._flip_factors()
        probe = QTransform()
        probe.rotate(float(self.effects.get("rotation", 0)))
        probe.scale(fx, fy)
        bounds = probe.mapRect(self.crop_rect)
        view = self.viewport()
        if bounds.width() > 0 and bounds.height() > 0 and view.width() > 4 and view.height() > 4:
            factor = min((view.width() - 4) / bounds.width(), (view.height() - 4) / bounds.height())
            self.apply_view_transform(factor)
            self.centerOn(self.crop_rect.center())
        else:
            self.apply_view_transform(self.view_scale() or 1.0)
        self.zoomChanged.emit(self.zoom_percent())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.zoomChanged.emit(self.zoom_percent())

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
            if .03 < self.view_scale() * factor < 20:
                self.scale(factor, factor)
                self.zoomChanged.emit(self.zoom_percent())
            event.accept()
        else:
            super().wheelEvent(event)

    def contextMenuEvent(self, event):
        if self.tool != "select":
            return
        item = self.itemAt(event.pos())
        if isinstance(item, Annotation) and not item.isSelected():
            self.scene().clearSelection()
            item.setSelected(True)
        window = self.window()
        menu = QMenu(self)
        selected = [x for x in self.scene().selectedItems() if isinstance(x, Annotation)]
        if selected:
            if hasattr(window, "annotation_properties"):
                menu.addAction("Eigenschaften …", window.annotation_properties)
            menu.addAction("Markierung kopieren", self.copy_annotations)
            menu.addAction("Markierung einfügen", self.paste_annotations)
            if any(x.data["kind"] == "stamp" for x in selected):
                menu.addAction("Originalgröße wiederherstellen", self.restore_stamp_size)
            if any(x.data["kind"] == "magnifier" for x in selected):
                menu.addAction("Lupenquelle sperren / lösen", self.toggle_magnifier_lock)
            if hasattr(window, "save_tool_defaults"):
                menu.addAction("Als Standardstil speichern", window.save_tool_defaults)
            menu.addSeparator()
            menu.addAction("Entfernen", self.delete_selected)
        else:
            menu.addAction("Markierung einfügen", self.paste_annotations)
            if hasattr(window, "replace_image"):
                menu.addAction("Hauptbild ersetzen …", window.replace_image)
            if hasattr(window, "canvas_dialog"):
                menu.addAction("Leinwandgröße …", window.canvas_dialog)
            if hasattr(window, "border_dialog"):
                menu.addAction("Rahmen …", window.border_dialog)
            if hasattr(window, "image_effects"):
                menu.addAction("Größe / Rahmen / Effekte …", window.image_effects)
        menu.exec(event.globalPos())

    def render_image(self):
        self.scene().clearSelection()
        bounds = QRectF(self.crop_rect)
        if self.effects.get("auto_canvas"):
            for item in self.annotations():
                if item.data["kind"] != "spotlight":
                    bounds = bounds.united(item.mapRectToScene(item.body_rect().adjusted(-item.data.get("width", 4), -item.data.get("width", 4), item.data.get("width", 4), item.data.get("width", 4))))
        rect = bounds.toAlignedRect()
        image = QImage(rect.size(), QImage.Format.Format_RGBA8888)
        if self.effects.get("auto_canvas") and not self.effects.get("transparent_background"):
            image.fill(QColor(self.effects.get("background", "#ffffff")))
        else:
            image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        draft_visible = self.preview is not None and self.preview.isVisible()
        if draft_visible:
            self.preview.hide()
        self._rendering_output = True
        self._update_base_pixmap(adjusted=False)
        try:
            self.scene().render(painter, QRectF(0, 0, rect.width(), rect.height()), QRectF(rect))
        finally:
            self._rendering_output = False
            self._update_base_pixmap(adjusted=True)
            painter.end()
            if draft_visible:
                self.preview.show()
        from .effects import apply_effects
        return apply_effects(qimage_to_pil(image, keep_alpha=True), self.effects)

    def project(self):
        r = self.crop_rect
        data = {"version": 1, "crop": [r.x(), r.y(), r.width(), r.height()],
                "annotations": [x.serialize() for x in self.annotations()], "effects": self.effects}
        if self.source_changed:
            buffer = io.BytesIO()
            self.source.save(buffer, format="PNG")
            data["replacement_image"] = base64.b64encode(buffer.getvalue()).decode()
        return data

    def load_project(self, data):
        if data.get("version") != 1:
            raise ValueError("Unbekannte Projektversion.")
        if data.get("replacement_image"):
            with Image.open(io.BytesIO(base64.b64decode(data["replacement_image"]))) as replacement:
                self.source = replacement.convert("RGBA" if "A" in replacement.getbands() else "RGB")
            self._base_key = None
            self.source_changed = True
        self.set_crop(QRectF(*data["crop"]).intersected(QRectF(0, 0, self.source.width, self.source.height)))
        from .effects import ImageEffects
        self.effects = ImageEffects.from_dict(data.get("effects", {})).to_dict()
        self.refresh_effects_preview()
        for annotation in data["annotations"]:
            self.add_annotation(annotation)
        self.undo.clear()


OPEN_EDITORS = []


class EditorWindow(QMainWindow):
    saved = Signal(object)
    projectSaved = Signal(object)
    navigate = Signal(int)

    def __init__(self, image, title="Aufnahme", project_path=None, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.title = title
        from . import __version__
        self.setWindowTitle(f"{title} — Editor · screenkaffee {__version__}")
        self.resize(1250, 850)
        self.project_path = Path(project_path) if project_path else None
        self.view = EditorView(image)
        self.setCentralWidget(self.view)
        self.commandbar = QToolBar("Bildaktionen", self)
        self.addToolBar(self.commandbar)
        self.addToolBarBreak()
        self.tools = QToolBar("Werkzeuge")
        self.tools.setMovable(False)
        self.addToolBar(self.tools)
        self.tool_combo = QComboBox()
        for label, tool in [("Auswählen / Verschieben", "select"), ("Pfeil", "arrow"),
            ("Linie", "line"), ("Rechteck", "rectangle"), ("Ellipse", "ellipse"),
            ("Text", "text"), ("Sprechblase", "speech"), ("Schrittnummer", "step"), ("Marker", "highlight"),
            ("Freihand", "freehand"), ("Polygon (Doppelklick beendet)", "polygon"), ("Klammer", "bracket"),
            ("Spotlight", "spotlight"), ("Vergrößerung", "magnifier"), ("Unschärfe", "blur"),
            ("Verpixeln", "pixelate"), ("Schwärzen", "redact"), ("Zuschneiden", "crop"),
            ("Streifen ausschneiden", "cutout"), ("Farbe ablesen", "picker"), ("Lineal (messen)", "measure")]:
            self.tool_combo.addItem(label, tool)
        self.tool_combo.currentIndexChanged.connect(lambda: self.view.set_tool(self.tool_combo.currentData()))
        self.build_palette()
        self.color_action = self.tools.addAction("Farbe", self.pick_color)
        self.color_action.setToolTip("Markierungsfarbe wählen")
        self.update_color_icon()
        self.fill_action = self.tools.addAction("Füllung", self.pick_quick_fill)
        self.fill_action.setToolTip("Füllfarbe für ausgewählte Form / aktuelles Werkzeug wählen (Abbrechen = transparent)")
        self.tools.addWidget(QLabel("  Stärke "))
        width = QSpinBox()
        width.setRange(1, 30)
        width.setValue(4)
        width.valueChanged.connect(lambda value: setattr(self.view, "width", value))
        self.tools.addWidget(width)
        self.tools.addWidget(QLabel("  Textgröße "))
        font = QSpinBox()
        font.setRange(8, 120)
        font.setValue(22)
        font.valueChanged.connect(lambda value: setattr(self.view, "font_size", value))
        self.tools.addWidget(font)
        self.width_box, self.font_box = width, font
        self.shadow_check = QCheckBox("Schatten")
        self.shadow_check.setToolTip("Schlagschatten für ausgewählte Markierung / aktuelles Werkzeug ein-/ausschalten")
        self.shadow_check.toggled.connect(self.toggle_quick_shadow)
        self.tools.addWidget(self.shadow_check)
        self.line_style_combo = QComboBox()
        self.line_style_combo.setToolTip("Linienart")
        for label, value in [("Durchgezogen", "solid"), ("Gestrichelt", "dash"), ("Gepunktet", "dot")]:
            self.line_style_combo.addItem(label, value)
        self.line_style_combo.currentIndexChanged.connect(self.set_quick_line_style)
        self.tools.addWidget(self.line_style_combo)
        self.arrow_style_combo = QComboBox()
        self.arrow_style_combo.setToolTip("Pfeilspitze")
        for label, value in [("Pfeil: Gefüllt", "filled"), ("Pfeil: Offen", "open"), ("Pfeil: Beidseitig", "double")]:
            self.arrow_style_combo.addItem(label, value)
        self.arrow_style_combo.currentIndexChanged.connect(self.set_quick_arrow_style)
        self.tools.addWidget(self.arrow_style_combo)
        self.view.styleChanged.connect(self.sync_tool_style)
        self.tools.addAction("Vorlagen …", self.manage_presets)
        self.tools.addSeparator()
        undo = self.tools.addAction("Rückgängig", self.view.undo.undo)
        undo.setShortcut(QKeySequence.StandardKey.Undo)
        undo.setEnabled(False)
        self.view.undo.canUndoChanged.connect(undo.setEnabled)
        redo = self.tools.addAction("Wiederholen", self.view.undo.redo)
        redo.setShortcut(QKeySequence.StandardKey.Redo)
        redo.setEnabled(False)
        self.view.undo.canRedoChanged.connect(redo.setEnabled)
        self.tools.addAction("Entfernen", self.view.delete_selected)
        self.tools.addAction("Eigenschaften", self.annotation_properties)
        self.tools.addAction("Auswahl skalieren", self.scale_selected)
        extra = QMenu("Weitere Bildaktionen", self)
        extra.addAction("Bild / Stempel einfügen …", self.insert_image)
        extra.addAction("Stempelbibliothek …", self.open_stamps)
        self.templates_menu = extra.addMenu("Vorlagen (Markierungen)")
        self.templates_menu.aboutToShow.connect(self.fill_templates_menu)
        self.fill_templates_menu()
        extra.addAction("Werkzeugstandards speichern", self.save_tool_defaults)
        extra.addAction("Hauptbild ersetzen …", self.replace_image)
        extra.addAction("In externem Editor öffnen …", self.open_external)
        extra.addAction("Bereich ausschneiden (Pixelwerte / Gezackt) …", self.cut_out_dialog)
        extra.addAction("Größe / Rahmen / Effekte …", self.image_effects)
        extra.addAction("Ausgabe-Vorschau (live)", self.preview_output)
        extra.addAction("Anheften (immer im Vordergrund)", self.pin_current)
        extra.addAction("Schicker Screenshot (Hintergrund, Rahmen) …", self.beautify_current)
        extra.addAction("Anderes Bild öffnen … (Strg+O)", self.open_image_file)
        extra.addAction("Vollbild (F11)", self.toggle_fullscreen)
        self.magnet_action = extra.addAction("Magnet-Ausrichtung beim Verschieben")
        self.magnet_action.setCheckable(True)
        self.magnet_action.setChecked(True)
        self.magnet_action.toggled.connect(lambda on: setattr(self.view, "magnet", on))
        self.auto_save = False
        self.auto_save_action = extra.addAction("Automatisch speichern")
        self.auto_save_action.setCheckable(True)
        self.auto_save_action.toggled.connect(lambda on: setattr(self, "auto_save", on))
        self.view.undo.indexChanged.connect(self.autosave)
        for keys, handler in (("Ctrl+O", self.open_image_file), ("F11", self.toggle_fullscreen),
                              ("Ctrl++", lambda: self.change_item_size(1)), ("Ctrl+=", lambda: self.change_item_size(1)),
                              ("Ctrl+-", lambda: self.change_item_size(-1))):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(handler)
        extra.addAction("Markierung kopieren", self.view.copy_annotations)
        extra.addAction("Markierung einfügen", self.view.paste_annotations)
        extra.addAction("Lupenquelle sperren / lösen", self.view.toggle_magnifier_lock)
        extra.addAction("Originalgröße wiederherstellen (Stempel)", self.view.restore_stamp_size)
        more = QToolButton()
        more.setText("Mehr")
        more.setMenu(extra)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tools.addWidget(more)
        exportbar = self.commandbar
        exportbar.setObjectName("editorCommands")
        exportbar.setMovable(False)
        exportbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        from PySide6.QtCore import QSize
        from .icons import command_icon
        exportbar.setIconSize(QSize(28, 28))
        copy_close = exportbar.addAction("Kopieren && Schließen", self.copy_and_close)
        copy_close.setIcon(command_icon("copy", "#5b9b47"))
        copy_close.setToolTip("Bearbeitung sichern, Bild in die Zwischenablage legen und den Editor schließen")
        btn = exportbar.widgetForAction(copy_close)
        if btn is not None:
            btn.setObjectName("copyAndClose")
        action = exportbar.addAction("Bild exportieren …", self.export)
        action.setIcon(command_icon("save", "#669e46"))
        action.setShortcut(QKeySequence.StandardKey.SaveAs)
        action = exportbar.addAction("Drucken", self.print_image)
        action.setIcon(command_icon("print", "#777777"))
        action.setShortcut(QKeySequence.StandardKey.Print)
        action = exportbar.addAction("Bildinfo", self.image_properties)
        action.setIcon(command_icon("properties", "#548daf"))
        action = exportbar.addAction("Bild kopieren", self.copy)
        action.setIcon(command_icon("copy", "#548daf"))
        action = exportbar.addAction("Projekt speichern", self.save_project)
        action.setIcon(command_icon("save", "#548daf"))
        action.setShortcut(QKeySequence.StandardKey.Save)
        action = exportbar.addAction("Als neue Aufnahme", lambda: self.saved.emit(self.view.render_image()))
        action.setIcon(command_icon("image", "#548daf"))
        exportbar.addSeparator()
        for action, name in ((undo, "undo"), (redo, "redo")):
            self.tools.removeAction(action)
            action.setIcon(command_icon(name, "#ca8c35"))
            exportbar.addAction(action)
        self.crop_action = exportbar.addAction("Zuschneiden", lambda: self.select_tool("crop" if self.view.tool != "crop" else "select"))
        self.crop_action.setCheckable(True)
        self.crop_action.setIcon(command_icon("crop", "#548daf"))
        self.crop_action.setToolTip("Bild zuschneiden: Rahmen aufziehen oder blaue Kanten-/Eckpunkte ziehen (Umschalt = 1:1, Esc bricht ab)")
        self.rotate_menu = QMenu("Drehen", self)
        action = self.rotate_menu.addAction("Rechts drehen", lambda: self.rotate_image(90))
        action.setIcon(command_icon("rotate_right", "#548daf"))
        action = self.rotate_menu.addAction("Links drehen", lambda: self.rotate_image(-90))
        action.setIcon(command_icon("rotate_left", "#548daf"))
        rotate = exportbar.addAction("Drehen", lambda: self.rotate_image(90))
        rotate.setIcon(command_icon("rotate_right", "#548daf"))
        rotate.setToolTip("Klick: rechts drehen · Pfeil: links drehen")
        rotate.setMenu(self.rotate_menu)
        button = exportbar.widgetForAction(rotate)
        if button is not None:
            button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.cutout_action = exportbar.addAction("Ausschneiden", lambda: self.select_tool("cutout" if self.view.tool != "cutout" else "select"))
        self.cutout_action.setCheckable(True)
        self.cutout_action.setIcon(command_icon("cutout", "#548daf"))
        self.cutout_action.setToolTip("Streifen direkt mit der Maus im Bild markieren und ausschneiden (Umschalt = gezackter Schnitt)")
        action = exportbar.addAction("Größe / Effekte", self.image_effects)
        action.setIcon(command_icon("effects", "#548daf"))
        action = exportbar.addAction("Leinwand", self.canvas_dialog)
        action.setIcon(command_icon("canvas", "#548daf"))
        action.setToolTip("Leinwandgröße: Außenabstand und Hintergrund")
        action = exportbar.addAction("Rahmen", self.border_dialog)
        action.setIcon(command_icon("border", "#c93b2b"))
        action.setToolTip("Rahmen um das Bild: Breite, Farbe und Eckenradius")
        # Navigation and zoom live in the status bar next to the zoom slider; the actions stay on the window for their shortcuts
        self.view_actions = []
        for text, step, key, icon_name in (("Vorheriges Bild", -1, "PgUp", "prev"), ("Nächstes Bild", 1, "PgDown", "next")):
            action = QAction(command_icon(icon_name, "#9a9a9a"), text, self)
            action.triggered.connect(lambda _=False, step=step: self.navigate.emit(step))
            action.setShortcut(QKeySequence(key))
            action.setToolTip(f"{text} im Arbeitsbereich ({key}; Alt+Umschalt löscht aktuelles Bild)")
            self.view_actions.append(action)
        for text, handler, icon_name in (("Einpassen", self.fit, "fit"), ("100 %", self.actual_size, "zoom_100")):
            action = QAction(command_icon(icon_name, "#9a9a9a"), text, self)
            action.triggered.connect(lambda _=False, handler=handler: handler())
            action.setToolTip(text)
            self.view_actions.append(action)
        self.addActions(self.view_actions)
        self.build_status_widgets()
        self.statusBar().showMessage("Strg + Mausrad: Zoom · Entf: Markierung entfernen · Text: Doppelklick · Schwärzen für vertrauliche Daten")
        self.view.colorPicked.connect(self.color_picked)
        self.view.measured.connect(lambda text: self.statusBar().showMessage("Lineal: " + text))
        self.view.toolFinished.connect(lambda: self.select_tool("select"))
        self.view.toolChanged.connect(self._on_tool_changed)
        if self.project_path and self.project_path.exists():
            try:
                self.view.load_project(json.loads(self.project_path.read_text(encoding="utf-8")))
            except Exception as exc:
                QMessageBox.warning(self, "Projekt konnte nicht geladen werden", str(exc))

    def _apply_quick_property(self, updates):
        selected = [item for item in self.view.scene().selectedItems() if isinstance(item, Annotation)]
        if self.view.tool not in ("select", "crop", "cutout", "picker"):
            self.view.tool_defaults.setdefault(self.view.tool, {}).update(updates)
        if not selected:
            return
        self.view.undo.beginMacro("Schnellstil ändern")
        for item in selected:
            before = item.serialize()
            after = dict(before, **updates)
            self.view.undo.push(DataCommand(item, before, after))
        self.view.undo.endMacro()

    def toggle_quick_shadow(self, checked):
        self._apply_quick_property({"shadow_radius": 8 if checked else 0, "shadow_offset": 4 if checked else 0})

    def pick_quick_fill(self):
        color = QColorDialog.getColor(QColor(self.view.color), self, "Füllfarbe wählen (Abbrechen = keine Füllung)")
        self._apply_quick_property({"fill": color.name() if color.isValid() else ""})

    def set_quick_line_style(self):
        self._apply_quick_property({"line_style": self.line_style_combo.currentData()})

    def set_quick_arrow_style(self):
        self._apply_quick_property({"arrow_style": self.arrow_style_combo.currentData()})

    def select_tool(self, tool):
        idx = self.tool_combo.findData(tool)
        if idx >= 0 and self.tool_combo.currentIndex() != idx:
            self.tool_combo.setCurrentIndex(idx)
        elif self.view.tool != tool:
            self.view.set_tool(tool)
        else:
            self._on_tool_changed(tool)

    def _on_tool_changed(self, tool):
        idx = self.tool_combo.findData(tool)
        if idx >= 0 and self.tool_combo.currentIndex() != idx:
            self.tool_combo.blockSignals(True)
            self.tool_combo.setCurrentIndex(idx)
            self.tool_combo.blockSignals(False)
        if tool in getattr(self, "palette_actions", {}):
            self.palette_actions[tool].setChecked(True)
        elif "select" in getattr(self, "palette_actions", {}) and tool == "cutout":
            for b in self.palette_actions.values():
                b.group().setExclusive(False)
                b.setChecked(False)
                b.group().setExclusive(True)
        if hasattr(self, "crop_action"):
            self.crop_action.setChecked(tool == "crop")
        if hasattr(self, "cutout_action"):
            self.cutout_action.setChecked(tool == "cutout")
        if hasattr(self, "status_hint"):
            if tool == "crop":
                self.statusBar().showMessage("Zuschneiden aktiv: Rahmen im Bild aufziehen oder blaue Kanten/Ecken nach innen ziehen (Umschalt = 1:1, Esc zum Abbrechen)")
            elif tool == "cutout":
                self.statusBar().showMessage("Ausschneiden aktiv: Ziehen Sie waagerecht oder senkrecht über den zu entfernenden Bildstreifen (Umschalt = gezackter Schnitt, Esc bricht ab)")
            elif tool == "measure":
                self.statusBar().showMessage("Lineal: Linie ziehen, um Abstände zu messen (Umschalt = waagerecht/senkrecht/45°). Wird nicht ins Bild übernommen.")
            elif tool == "select":
                self.statusBar().showMessage("Strg + Mausrad: Zoom · Entf: Markierung entfernen · Text: Doppelklick · Schwärzen für vertrauliche Daten")

    def build_status_widgets(self):
        from PySide6.QtWidgets import QSlider
        self.size_label = QLabel()
        self.size_label.setMinimumWidth(84)
        self.statusBar().addPermanentWidget(self.size_label)
        self.status_hint = QLabel()
        self.status_hint.setObjectName("muted")
        self.statusBar().addPermanentWidget(self.status_hint, 1)
        self.statusBar().messageChanged.connect(self.status_hint.setText)
        self.zoom_label = QLabel("100 %")
        self.zoom_label.setMinimumWidth(48)
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(10, 400)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setFixedWidth(150)
        self.zoom_slider.valueChanged.connect(self.zoom_to)
        for action in getattr(self, "view_actions", []):
            button = QToolButton()
            button.setObjectName("statusButton")
            button.setDefaultAction(action)
            button.setAutoRaise(True)
            button.setIconSize(QSize(18, 18))
            self.statusBar().addPermanentWidget(button)
        self.statusBar().addPermanentWidget(self.zoom_label)
        self.statusBar().addPermanentWidget(self.zoom_slider)
        self.view.zoomChanged.connect(self.zoom_shown)
        self.update_size_label()

    def update_size_label(self):
        rect = self.view.crop_rect
        self.size_label.setText(f"{round(rect.width())} × {round(rect.height())}   ")

    def zoom_to(self, percent):
        if abs(self.view.zoom_percent() - percent) > 1:
            dpr = self.view._screen_dpr()
            self.view.apply_view_transform(percent / 100 / dpr)
            self.view.zoomChanged.emit(self.view.zoom_percent())

    def zoom_shown(self, percent):
        self.zoom_label.setText(f"{round(percent)} %")
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(max(10, min(400, round(percent))))
        self.zoom_slider.blockSignals(False)
        self.update_size_label()

    def print_image(self):
        from .printing import print_images
        try:
            print_images(self, [self.view.render_image()])
        except Exception as exc:
            QMessageBox.warning(self, "Drucken", str(exc))

    def image_properties(self):
        rect = self.view.crop_rect
        source = self.view.source
        lines = [f"Größe: {round(rect.width())} × {round(rect.height())} Pixel",
                 f"Originalbild: {source.width} × {source.height} Pixel ({source.mode})",
                 f"Markierungen: {len(self.view.annotations())}",
                 f"Projektdatei: {self.project_path or 'noch nicht gespeichert'}"]
        QMessageBox.information(self, "Bildeigenschaften", "\n".join(lines))

    def cut_out_dialog(self):
        from .effects import cut_out
        dialog = QDialog(self)
        dialog.setWindowTitle("Bereich ausschneiden")
        form = QFormLayout(dialog)
        axis = QComboBox()
        axis.addItem("Waagerechten Streifen entfernen (von … bis Zeile)", "y")
        axis.addItem("Senkrechten Streifen entfernen (von … bis Spalte)", "x")
        form.addRow("Richtung", axis)
        start, end, gap_box, torn_box = QSpinBox(), QSpinBox(), QSpinBox(), QSpinBox()
        for box in (start, end):
            box.setRange(0, 100000)
        gap_box.setRange(0, 200)
        torn_box.setRange(0, 100)
        end.setValue(min(100, max(2, self.view.source.height // 3)))
        form.addRow("Von (Pixel)", start)
        form.addRow("Bis (Pixel)", end)
        form.addRow("Abstand / Lücke (Pixel)", gap_box)
        form.addRow("Gezackte Schnittkante (0 = glatt)", torn_box)
        preview_label = QLabel()
        preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_label.setMinimumSize(320, 180)
        form.addRow("Live-Vorschau", preview_label)
        def refresh_cut_preview(*_):
            try:
                img = cut_out(self.view.source, axis.currentData(), start.value(), end.value(), gap=gap_box.value(), torn=torn_box.value())
                preview_label.setPixmap(QPixmap.fromImage(pil_to_qimage(img)).scaled(320, 180, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            except ValueError as exc:
                preview_label.setText(str(exc))
        for ctrl in (start, end, gap_box, torn_box):
            ctrl.valueChanged.connect(refresh_cut_preview)
        axis.currentIndexChanged.connect(refresh_cut_preview)
        refresh_cut_preview()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            image = cut_out(self.view.source, axis.currentData(), start.value(), end.value(), gap=gap_box.value(), torn=torn_box.value())
        except ValueError as exc:
            QMessageBox.warning(self, "Ausschneiden", str(exc))
            return
        self.view.undo.push(CutOutCommand(self.view, image, axis.currentData(), start.value(), end.value()))
        self.fit()

    def showEvent(self, event):
        super().showEvent(event)
        self.fit()

    def fit(self):
        self.view.fit_to_crop()

    def actual_size(self):
        dpr = self.view._screen_dpr()
        self.view.apply_view_transform(1.0 / dpr)
        self.view.zoomChanged.emit(100.0)

    TOOL_TIPS = {"select": "Auswählen / Verschieben", "arrow": "Pfeil", "line": "Linie", "rectangle": "Rechteck",
                 "ellipse": "Ellipse", "text": "Text", "speech": "Sprechblase", "step": "Schrittnummer",
                 "highlight": "Marker", "freehand": "Freihand", "polygon": "Polygon (Doppelklick beendet)",
                 "bracket": "Klammer", "spotlight": "Spotlight", "magnifier": "Vergrößerung", "blur": "Unschärfe",
                 "pixelate": "Verpixeln", "redact": "Schwärzen", "crop": "Zuschneiden", "picker": "Farbe ablesen",
                 "measure": "Lineal: Abstände in Pixeln messen (Umschalt = gerade)"}

    def build_palette(self):
        from PySide6.QtWidgets import QGridLayout, QButtonGroup
        palette = QToolBar("Werkzeugleiste")
        palette.setObjectName("tools")
        palette.setOrientation(Qt.Orientation.Vertical)
        palette.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, palette)
        panel = QWidget()
        grid = QGridLayout(panel)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setSpacing(1)
        self.palette_actions = {}
        group = QButtonGroup(panel)
        group.setExclusive(True)
        from PySide6.QtWidgets import QFrame
        groups = [["select", "crop"], ["arrow", "line", "rectangle", "ellipse", "freehand", "polygon", "bracket"],
                  ["text", "speech", "step"], ["highlight", "spotlight", "magnifier"], ["blur", "pixelate", "redact"], ["picker", "measure"]]
        row = 0
        for number, names in enumerate(groups):
            if number:
                line = QFrame()
                line.setObjectName("toolSeparator")
                line.setFixedSize(24, 1)
                line.setStyleSheet("background: #4a5560;")
                grid.addWidget(line, row, 0, Qt.AlignmentFlag.AlignHCenter)
                grid.setRowMinimumHeight(row, 6)
                row += 1
            for name in names:
                self.add_palette_button(grid, group, name, row)
                row += 1
        grid.setRowStretch(row, 1)
        self.palette_actions["select"].setChecked(True)
        palette.addWidget(panel)
        self.tool_combo.currentIndexChanged.connect(
            lambda: self._on_tool_changed(self.tool_combo.currentData()))

    def add_palette_button(self, grid, group, name, row):
        from PySide6.QtWidgets import QToolButton
        from PySide6.QtCore import QSize
        from .icons import tool_icon
        button = QToolButton()
        button.setIcon(tool_icon(name, color="#d9e9f2", active="#ffffff"))
        button.setIconSize(QSize(20, 20))
        button.setFixedSize(28, 28)                 # 28 statt 32: 20 Werkzeuge + Gruppenlinien nicht höher als früher
        button.setCheckable(True)
        button.setToolTip(self.TOOL_TIPS[name])
        button.setStatusTip(self.TOOL_TIPS[name])
        group.addButton(button)
        button.clicked.connect(lambda _=False, n=name: self.select_tool(n))
        grid.addWidget(button, row, 0)
        self.palette_actions[name] = button

    def update_color_icon(self):
        from PySide6.QtGui import QIcon
        pixmap = QPixmap(22, 22)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(self.view.color))
        painter.setPen(QPen(QColor("#e6edf7"), 2))
        painter.drawRoundedRect(2, 2, 18, 18, 5, 5)
        painter.end()
        self.color_action.setIcon(QIcon(pixmap))

    def manage_presets(self):
        from .presets import load_presets, save_preset, delete_preset
        from PySide6.QtWidgets import QInputDialog
        presets = load_presets()
        items = ["Aktuelle Werkzeugeinstellung speichern …", "Vorlage löschen …"] + list(presets)
        choice, ok = QInputDialog.getItem(self, "Werkzeugvorlagen", "Aktion oder Vorlage:", items, 0, False)
        if not ok:
            return
        if choice == items[0]:
            name, ok = QInputDialog.getText(self, "Vorlage speichern", "Name:")
            if ok and name.strip():
                save_preset(name, {"tool": self.view.tool, "color": self.view.color,
                                   "width": self.view.width, "font_size": self.view.font_size})
        elif choice == items[1] and presets:
            name, ok = QInputDialog.getItem(self, "Vorlage löschen", "Vorlage:", list(presets), 0, False)
            if ok:
                delete_preset(name)
        elif choice in presets:
            self.apply_preset(presets[choice])

    def apply_preset(self, values):
        index = self.tool_combo.findData(values.get("tool"))
        if index >= 0:
            self.tool_combo.setCurrentIndex(index)
        self.view.color = values.get("color", self.view.color)
        self.update_color_icon()
        self.width_box.setValue(int(values.get("width", self.view.width)))
        self.font_box.setValue(int(values.get("font_size", self.view.font_size)))

    def sync_tool_style(self):
        self.width_box.setValue(round(self.view.width))
        self.font_box.setValue(round(self.view.font_size))
        self.update_color_icon()

    def save_tool_defaults(self):
        from .presets import save_defaults
        self.view.remember_tool_style()
        try:
            save_defaults(self.view.tool_defaults)
            self.statusBar().showMessage("Standardstile der Werkzeuge gespeichert")
        except OSError as exc:
            QMessageBox.warning(self, "Werkzeugstandards", str(exc))

    def pick_color(self):
        color = QColorDialog.getColor(QColor(self.view.color), self, "Markierungsfarbe")
        if color.isValid():
            self.view.color = color.name()
            self.update_color_icon()

    def color_picked(self, value):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(value)
        self.statusBar().showMessage(f"{value} in Zwischenablage")

    def copy(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setImage(pil_to_qimage(self.view.render_image()))
        self.statusBar().showMessage("Bearbeitetes Bild in Zwischenablage")

    def copy_and_close(self, everywhere=None):
        """Copy & Exit: keep the markup, put the edited image on the clipboard, close.
        With Ctrl+Alt held (or everywhere=True) every open editor does the same; the last copy stays on the clipboard."""
        if everywhere is None:
            held = QApplication.keyboardModifiers()
            everywhere = bool(held & Qt.KeyboardModifier.ControlModifier and held & Qt.KeyboardModifier.AltModifier)
        windows = [w for w in QApplication.topLevelWidgets() if isinstance(w, EditorWindow) and w is not self] if everywhere else []
        for window in windows:
            window.copy_and_close(everywhere=False)
        self.copy()
        if self.project_path is not None and not self.view.undo.isClean() and not self.save_project():
            return
        self.close()

    def open_image_file(self, path=None):
        """Open any image in a new editor window (Ctrl+O, or "open with" from the file manager)."""
        from .color import import_srgb
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Bild öffnen", "", "Bilder (*.png *.jpg *.jpeg *.webp *.jxl *.bmp *.tif *.tiff *.gif)")
        if not path:
            return None
        try:
            window = EditorWindow(import_srgb(path), Path(path).name)
        except Exception as exc:
            QMessageBox.warning(self, "Bild konnte nicht geöffnet werden", str(exc))
            return None
        OPEN_EDITORS.append(window)
        window.destroyed.connect(lambda _=None, w=window: OPEN_EDITORS.remove(w) if w in OPEN_EDITORS else None)
        window.show()
        return window

    def rotate_image(self, degrees):
        """Rotate the finished image in 90° steps (applied with the other image effects; Undo-able)."""
        rotation = (int(self.view.effects.get("rotation", 0)) + int(degrees) + 180) % 360 - 180
        self.view.undo.push(EffectsCommand(self.view, dict(self.view.effects, rotation=rotation)))
        return rotation

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def change_item_size(self, direction):
        """Ctrl+Plus/Minus: selected text/step items grow or shrink; otherwise the default text size changes."""
        selected = [item for item in self.view.scene().selectedItems() if isinstance(item, Annotation)]
        changed = False
        if selected:
            self.view.undo.beginMacro("Größe ändern")
        for item in selected:
            before = item.serialize()
            after = dict(before)
            if item.data["kind"] in ("text", "speech"):
                after["font_size"] = max(6, min(200, round(before.get("font_size", 22)) + 2 * direction))
            elif item.data["kind"] == "step":
                size = max(20, min(300, round(before.get("text_width", 46)) + 4 * direction))
                after["text_width"] = after["text_height"] = size
            else:
                continue
            if after != before:
                self.view.undo.push(DataCommand(item, before, after))
                changed = True
        if selected:
            self.view.undo.endMacro()
        if not selected:
            self.view.font_size = max(6, min(200, self.view.font_size + 2 * direction))
            self.statusBar().showMessage(f"Standard-Textgröße: {self.view.font_size} px")
        return changed

    def autosave(self, *_):
        if self.auto_save and self.project_path is not None and not self.view.undo.isClean():
            self.save_project()

    def suggested_name(self):
        from .batch import safe_name
        return safe_name(self.title)

    def export(self):
        path, selected = QFileDialog.getSaveFileName(self, "Bild exportieren", self.suggested_name() + ".png", "PNG (*.png);;JPEG (*.jpg);;WebP (*.webp);;JPEG XL (*.jxl);;TIFF (*.tiff);;BMP (*.bmp);;GIF (*.gif)")
        if path:
            try:
                if not Path(path).suffix:
                    import re
                    path += re.search(r"\*([.a-z]+)", selected).group(1)
                save_image(self.view.render_image(), path, metadata={"Software": "screenkaffee"})
                self.statusBar().showMessage(f"Bild gespeichert: {path}")
            except Exception as exc:
                QMessageBox.critical(self, "Export fehlgeschlagen", str(exc))

    def save_project(self):
        if self.project_path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Projekt speichern", self.suggested_name() + ".screenkaffee.json", "screenkaffee-Projekt (*.screenkaffee.json *.prism.json)")
            if not path:
                return False
            self.project_path = Path(path)
        try:
            temp = self.project_path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.view.project(), ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.project_path)
            self.view.undo.setClean()
            self.projectSaved.emit(self.view.render_image())
            self.statusBar().showMessage("Markierungen gespeichert. Original bleibt für erneute Bearbeitung erhalten.")
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Speichern fehlgeschlagen", str(exc))
            return False

    def insert_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Bild / Stempel einfügen", "", "Bilder (*.png *.jpg *.jpeg *.webp *.jxl *.bmp)")
        if path:
            from .color import import_srgb
            self.view.insert_stamp(import_srgb(path))

    def open_stamps(self):
        from .stamps import choose_stamp
        image = choose_stamp(self)
        if image is not None:
            self.view.insert_stamp(image)

    def fill_templates_menu(self):
        from .templates import TemplateLibrary
        menu = self.templates_menu
        menu.clear()
        library = TemplateLibrary()
        for entry in library.entries():
            menu.addAction(entry["name"], lambda e=entry: self.insert_template(e))
        menu.addSeparator()
        menu.addAction("Auswahl als Vorlage speichern …", self.save_template)
        own = library.own()
        if own:
            delete = menu.addMenu("Eigene Vorlage löschen")
            for entry in own:
                delete.addAction(entry["name"], lambda name=entry["name"]: self.delete_template(name))

    def insert_template(self, entry):
        """Vorlage in der Bildmitte einfügen; die eingefügten Markierungen sind danach ausgewählt."""
        from .templates import placed
        center = self.view.crop_rect.center()
        self.view.scene().clearSelection()
        self.view.undo.beginMacro("Vorlage einfügen: " + entry["name"])
        items = [self.view.add_annotation(data) for data in placed(entry["items"], (center.x(), center.y()))]
        self.view.undo.endMacro()
        for item in items:
            item.setSelected(True)
        self.statusBar().showMessage(f"Vorlage „{entry['name']}“ eingefügt – Text per Doppelklick ändern")
        return items

    def save_template(self, name=None):
        from .templates import TemplateLibrary
        items = [x.serialize() for x in self.view.annotations() if x.isSelected()]
        if not items:
            QMessageBox.information(self, "Vorlage speichern", "Bitte zuerst eine oder mehrere Markierungen auswählen.")
            return None
        if name is None:
            name, ok = QInputDialog.getText(self, "Vorlage speichern", f"Name für die Vorlage ({len(items)} Markierungen):")
            if not ok:
                return None
        try:
            saved = TemplateLibrary().add(name, items)
        except ValueError as exc:
            QMessageBox.warning(self, "Vorlage speichern", str(exc))
            return None
        self.statusBar().showMessage(f"Vorlage „{saved}“ gespeichert – zu finden unter Weitere Bildaktionen → Vorlagen")
        return saved

    def delete_template(self, name):
        from .templates import TemplateLibrary
        if QMessageBox.question(self, "Vorlage löschen", f"Eigene Vorlage „{name}“ löschen?") == QMessageBox.StandardButton.Yes:
            TemplateLibrary().remove(name)

    def replace_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Hauptbild ersetzen; Markierungen bleiben erhalten", "", "Bilder (*.png *.jpg *.jpeg *.webp *.jxl *.bmp)")
        if path:
            from .color import import_srgb
            self.view.undo.push(SourceCommand(self.view, import_srgb(path)))
            self.fit()

    def open_external(self):
        from .policy import load_policy
        if "external_editor" in load_policy()["disabled"]:
            QMessageBox.information(self, "Richtlinie", "Diese Funktion ist per Richtlinie deaktiviert.")
            return
        from . import external
        from PySide6.QtCore import QTimer, QSettings
        program, _ = QFileDialog.getOpenFileName(self, "Programm zum Bearbeiten wählen")
        if not program:
            return
        try:
            path = external.export_temp(self.view.source.convert("RGBA" if "A" in self.view.source.getbands() else "RGB"))
            external.launch(program, path)
        except Exception as exc:
            QMessageBox.warning(self, "Externer Editor", str(exc))
            return
        self.external_path = path
        self.external_mtime = path.stat().st_mtime_ns
        timer = getattr(self, "external_timer", None) or QTimer(self)
        self.external_timer = timer
        try:
            timer.timeout.disconnect()
        except RuntimeError:
            pass
        timer.timeout.connect(self.check_external)
        timer.start(1000)
        self.statusBar().showMessage("Externer Editor geöffnet · Speichern dort übernimmt das Bild hier automatisch")

    def check_external(self):
        from . import external
        mtime = external.changed(self.external_path, self.external_mtime)
        if mtime is None:
            return
        try:
            from .color import import_srgb
            self.view.undo.push(SourceCommand(self.view, import_srgb(self.external_path)))
            self.external_mtime = mtime
            self.fit()
            self.statusBar().showMessage("Bild aus externem Editor übernommen")
        except Exception:
            pass  # file may be mid-write; mtime stays old so the next tick retries

    def beautify_current(self):
        from .beautify import beautify_dialog
        settings = getattr(self, "beautify_settings", {})
        self.beautify_settings = settings
        dialog = beautify_dialog(self, self.view.render_image(), settings, on_save=self.saved.emit, title=self.title or "")
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        return dialog

    def pin_current(self):
        from .pin import pin
        return pin(self.view.render_image(), self.title or "")

    def preview_output(self):
        """Non-modal live preview of the finished image (all effects, incl. shadow/border/size/watermark).

        Stays open next to the editor and refreshes (debounced) after every change on the undo stack."""
        existing = getattr(self, "_output_preview", None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return existing
        dialog = QDialog(self)
        dialog.setWindowTitle("Ausgabe mit Bildeffekten (live)")
        dialog.setModal(False)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.resize(1000, 750)
        layout = QVBoxLayout(dialog)
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumSize(200, 150)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        info = QLabel()
        layout.addWidget(label, 1)
        layout.addWidget(info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)
        timer = QTimer(dialog)
        timer.setSingleShot(True)
        timer.setInterval(150)
        state = {"image": None}

        def render():
            state["image"] = self.view.render_image()
            show()

        def show():
            image = state["image"]
            if image is None:
                return
            label.setPixmap(QPixmap.fromImage(pil_to_qimage(image)).scaled(
                max(1, label.width()), max(1, label.height()), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            info.setText(f"Ausgabegröße: {image.width} × {image.height} Pixel (aktualisiert sich bei jeder Änderung)")

        timer.timeout.connect(render)
        refresh = lambda *_: timer.start()
        self.view.undo.indexChanged.connect(refresh)
        dialog.destroyed.connect(lambda *_: self.view.undo.indexChanged.disconnect(refresh))
        original_resize = dialog.resizeEvent
        dialog.resizeEvent = lambda event: (original_resize(event), show())[0]
        dialog.destroyed.connect(lambda *_: self._output_preview is dialog and setattr(self, "_output_preview", None))
        self._output_preview = dialog
        dialog.show()
        render()
        return dialog

    def image_effects(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Bildgröße, Rahmen und Effekte")
        form = QFormLayout(dialog)
        controls = {}
        definitions = [("scale_percent", "Größe in %", 5, 400), ("rotation", "Drehung im Uhrzeigersinn", -180, 180),
            ("padding", "Außenabstand in Pixeln", 0, 1000), ("corner_radius", "Eckenradius", 0, 500),
            ("border_width", "Rahmenbreite", 0, 100), ("shadow_radius", "Schattenweichheit", 0, 200),
            ("shadow_offset", "Schattenversatz", -200, 200), ("shadow_opacity", "Schatten-Deckkraft (0–255)", 0, 255),
            ("torn_edge", "Risskante", 0, 100), ("reflection_percent", "Spiegelung in %", 0, 100)]
        for key, label, low, high in definitions:
            widget = QSpinBox()
            widget.setRange(low, high)
            widget.setValue(self.view.effects[key])
            controls[key] = widget
            form.addRow(label, widget)
        for key, label in [("brightness", "Helligkeit"), ("contrast", "Kontrast")]:
            widget = QDoubleSpinBox()
            widget.setRange(.1, 3)
            widget.setSingleStep(.1)
            widget.setValue(self.view.effects[key])
            controls[key] = widget
            form.addRow(label, widget)
        for key, label in [("flip_horizontal", "Horizontal spiegeln"), ("flip_vertical", "Vertikal spiegeln"),
                           ("grayscale", "Graustufen"), ("transparent_background", "Transparenter Hintergrund"),
                           ("auto_canvas", "Automatische Leinwandgröße (wächst mit Markierungen)")]:
            widget = QCheckBox()
            widget.setChecked(bool(self.view.effects.get(key, False)))
            controls[key] = widget
            form.addRow(label, widget)
        for key, label in [("background", "Hintergrundfarbe (#RRGGBB)"), ("border_color", "Rahmenfarbe (#RRGGBB)"), ("watermark", "Wasserzeichen (Text)"), ("watermark_image", "Wasserzeichen (Bilddatei, PNG/JPG)")]:
            widget = QLineEdit(self.view.effects[key])
            controls[key] = widget
            form.addRow(label, widget)
        position = QComboBox()
        for v, vl in [("top", "Oben"), ("middle", "Mitte"), ("bottom", "Unten")]:
            for h, hl in [("left", "links"), ("center", "zentriert"), ("right", "rechts")]:
                position.addItem(f"{vl} {hl}", f"{v}-{h}")
        position.setCurrentIndex(max(0, position.findData(self.view.effects.get("watermark_position", "bottom-right"))))
        form.addRow("Wasserzeichen-Position", position)
        for key, label, low, high in [("watermark_scale", "Bild-Wasserzeichen Breite (%)", 1, 100), ("watermark_opacity", "Wasserzeichen-Deckkraft (0–255)", 0, 255)]:
            widget = QSpinBox()
            widget.setRange(low, high)
            widget.setValue(self.view.effects[key])
            controls[key] = widget
            form.addRow(label, widget)
        preview_label = QLabel()
        preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_label.setMinimumSize(320, 180)
        form.addRow("Live-Vorschau", preview_label)
        base_img = self.view.source.copy()
        base_img.thumbnail((320, 180))
        def refresh_effects_preview(*_):
            from .effects import apply_effects
            cand = dict(self.view.effects, watermark_position=position.currentData())
            for k, w in controls.items():
                cand[k] = w.isChecked() if isinstance(w, QCheckBox) else w.text() if isinstance(w, QLineEdit) else w.value()
            if QColor(cand.get("background", "#ffffff")).isValid() and QColor(cand.get("border_color", "#000000")).isValid():
                try:
                    out = apply_effects(base_img, cand)
                    preview_label.setPixmap(QPixmap.fromImage(pil_to_qimage(out)).scaled(320, 180, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                except Exception:
                    LOG.debug("Effekt-Vorschau fehlgeschlagen", exc_info=True)
        for w in controls.values():
            if isinstance(w, QCheckBox):
                w.toggled.connect(refresh_effects_preview)
            elif isinstance(w, QLineEdit):
                w.textChanged.connect(refresh_effects_preview)
            else:
                w.valueChanged.connect(refresh_effects_preview)
        position.currentIndexChanged.connect(refresh_effects_preview)
        refresh_effects_preview()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        data = dict(self.view.effects)
        data["watermark_position"] = position.currentData()
        for key, widget in controls.items():
            data[key] = widget.isChecked() if isinstance(widget, QCheckBox) else widget.text() if isinstance(widget, QLineEdit) else widget.value()
        for key in ("background", "border_color"):
            if not QColor(data[key]).isValid():
                QMessageBox.warning(self, "Ungültige Farbe", data[key])
                return
        self.view.undo.push(EffectsCommand(self.view, data))

    def quick_effects(self, title, numbers, colors, checks=()):
        """Small dialog over a few ImageEffects fields; applies them as one undoable step."""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        form = QFormLayout(dialog)
        controls = {}
        for key, label, low, high in numbers:
            widget = QSpinBox()
            widget.setRange(low, high)
            widget.setValue(self.view.effects[key])
            controls[key] = widget
            form.addRow(label, widget)
        for key, label in colors:
            widget = QLineEdit(self.view.effects[key])
            controls[key] = widget
            form.addRow(label, widget)
        for key, label in checks:
            widget = QCheckBox()
            widget.setChecked(self.view.effects[key])
            controls[key] = widget
            form.addRow(label, widget)
        preview_label = QLabel()
        preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_label.setMinimumSize(300, 160)
        form.addRow("Live-Vorschau", preview_label)
        base_img = self.view.source.copy()
        base_img.thumbnail((280, 150))
        def refresh_quick_preview(*_):
            from .effects import apply_effects
            cand = dict(self.view.effects, **{k: w.isChecked() if isinstance(w, QCheckBox) else w.text() if isinstance(w, QLineEdit) else w.value()
                                              for k, w in controls.items()})
            if QColor(cand.get("background", "#ffffff")).isValid() and QColor(cand.get("border_color", "#000000")).isValid():
                try:
                    out = apply_effects(base_img, cand)
                    preview_label.setPixmap(QPixmap.fromImage(pil_to_qimage(out)).scaled(300, 160, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                except Exception:
                    LOG.debug("Effekt-Vorschau fehlgeschlagen", exc_info=True)
        for w in controls.values():
            if isinstance(w, QCheckBox):
                w.toggled.connect(refresh_quick_preview)
            elif isinstance(w, QLineEdit):
                w.textChanged.connect(refresh_quick_preview)
            else:
                w.valueChanged.connect(refresh_quick_preview)
        refresh_quick_preview()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.apply_effect_values({key: widget.isChecked() if isinstance(widget, QCheckBox) else widget.text() if isinstance(widget, QLineEdit) else widget.value()
                                  for key, widget in controls.items()})

    def apply_effect_values(self, values):
        data = dict(self.view.effects, **values)
        for key in ("background", "border_color"):
            if not QColor(data[key]).isValid():
                QMessageBox.warning(self, "Ungültige Farbe", data[key])
                return False
        self.view.undo.push(EffectsCommand(self.view, data))
        return True

    def canvas_dialog(self):
        self.quick_effects("Leinwandgröße", [("padding", "Außenabstand in Pixeln", 0, 1000)],
                           [("background", "Hintergrundfarbe (#RRGGBB)")],
                           [("transparent_background", "Transparenter Hintergrund"),
                            ("auto_canvas", "Automatische Leinwandgröße (wächst mit Markierungen)")])

    def border_dialog(self):
        self.quick_effects("Rahmen", [("border_width", "Rahmenbreite (0 = kein Rahmen)", 0, 100), ("corner_radius", "Eckenradius", 0, 500)],
                           [("border_color", "Rahmenfarbe (#RRGGBB)")])

    def annotation_properties(self):
        selected = [item for item in self.view.scene().selectedItems() if isinstance(item, Annotation)]
        if not selected:
            self.statusBar().showMessage("Mit dem Auswahlwerkzeug zuerst eine Markierung auswählen.")
            return
        data = selected[0].serialize()
        dialog = QDialog(self)
        dialog.setWindowTitle("Markierung bearbeiten")
        form = QFormLayout(dialog)
        controls = {}
        for key, label, default in [("color", "Linienfarbe", "#ff5a36"), ("fill", "Füllfarbe (leer = transparent)", ""),
            ("font_family", "Schriftart", "Segoe UI"), ("text_color", "Textfarbe", data["color"]),
            ("glow_color", "Textglühen: Farbe", "#ffffff"), ("shadow_color", "Textschatten: Farbe", "#000000")]:
            widget = QLineEdit(data.get(key, default))
            controls[key] = widget
            form.addRow(label, widget)
        for key, label, default, low, high in [("width", "Linienstärke", 4, 1, 100), ("font_size", "Schriftgröße", 22, 6, 200),
            ("radius", "Eckenradius", 0, 0, 200), ("rotation", "Drehung", 0, -180, 180),
            ("blur", "Unschärfestärke", 16, 1, 100), ("zoom", "Vergrößerung", 2, 1, 10),
            ("glow_radius", "Textglühen: Radius", 0, 0, 30), ("shadow_radius", "Textschatten: Weichheit", 0, 0, 30),
            ("shadow_offset", "Textschatten: Versatz", 0, -50, 50)]:
            widget = QSpinBox()
            widget.setRange(low, high)
            widget.setValue(round(data.get(key, default)))
            controls[key] = widget
            form.addRow(label, widget)
        opacity = QDoubleSpinBox()
        opacity.setRange(.05, 1)
        opacity.setSingleStep(.05)
        opacity.setValue(data.get("opacity", 1))
        controls["opacity"] = opacity
        form.addRow("Deckkraft", opacity)
        for key, label, default in [("connect", "Schritt mit vorheriger Nummer verbinden", False), ("bold", "Fett", True), ("italic", "Kursiv", False), ("underline", "Unterstrichen", False)]:
            widget = QCheckBox()
            widget.setChecked(data.get(key, default))
            controls[key] = widget
            form.addRow(label, widget)
        arrow_style, line_style = QComboBox(), QComboBox()
        for label, value in [("Gefüllt", "filled"), ("Offen", "open"), ("Beidseitig", "double")]:
            arrow_style.addItem(label, value)
        for label, value in [("Durchgezogen", "solid"), ("Gestrichelt", "dash"), ("Gepunktet", "dot")]:
            line_style.addItem(label, value)
        arrow_style.setCurrentIndex(max(0, arrow_style.findData(data.get("arrow_style", "filled"))))
        line_style.setCurrentIndex(max(0, line_style.findData(data.get("line_style", "solid"))))
        curve = QSpinBox()
        curve.setRange(-200, 200)
        curve.setValue(round(data.get("curve", 0)))
        form.addRow("Pfeilspitze", arrow_style)
        form.addRow("Linienart", line_style)
        form.addRow("Krümmung (Pfeil/Linie)", curve)
        align = QComboBox()
        for label, value in [("Links", "left"), ("Mitte", "center"), ("Rechts", "right")]:
            align.addItem(label, value)
        align.setCurrentIndex(max(0, align.findData(data.get("align", "left"))))
        form.addRow("Textausrichtung", align)
        step_format = QComboBox()
        for label, value in [("1, 2, 3", "1"), ("A, B, C", "A"), ("I, II, III", "I")]:
            step_format.addItem(label, value)
        step_format.setCurrentIndex(max(0, step_format.findData(data.get("step_format", "1"))))
        if data["kind"] == "step":
            form.addRow("Zahlenformat (Schritte)", step_format)
        lock_sample = QCheckBox("Lupenquelle an aktueller Bildposition sperren")
        lock_sample.setChecked("sample_box" in data)
        if data["kind"] == "magnifier":
            form.addRow(lock_sample)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = {key: widget.isChecked() if isinstance(widget, QCheckBox) else widget.text() if isinstance(widget, QLineEdit) else widget.value()
                  for key, widget in controls.items()}
        for key in ("color", "fill", "text_color", "glow_color", "shadow_color"):
            if values[key] and not QColor(values[key]).isValid():
                QMessageBox.warning(self, "Ungültige Farbe", values[key])
                return
        values["align"] = align.currentData()
        values.update(arrow_style=arrow_style.currentData(), line_style=line_style.currentData(), curve=curve.value())
        self.view.undo.beginMacro("Markierungseigenschaften ändern")
        for item in selected:
            before = item.serialize()
            after = dict(before, **values)
            if item.data["kind"] == "step":
                after["step_format"] = step_format.currentData()
                try:
                    after["text"] = step_label(int(after.get("number") or after["text"]), after["step_format"])
                    after["number"] = int(after.get("number") or before["text"])
                except ValueError:
                    pass
            from .presets import STYLE_KEYS
            self.view.tool_defaults[item.data["kind"]] = {key: value for key, value in after.items() if key in STYLE_KEYS}
            if item.data["kind"] == "magnifier":
                if lock_sample.isChecked():
                    rect = item.body_rect()
                    center = rect.center() + item.pos()
                    zoom = values["zoom"]
                    after["sample_box"] = list(before.get("sample_box", [center.x() - rect.width() / zoom / 2,
                        center.y() - rect.height() / zoom / 2, center.x() + rect.width() / zoom / 2,
                        center.y() + rect.height() / zoom / 2]))
                else:
                    after.pop("sample_box", None)
            self.view.undo.push(DataCommand(item, before, after))
        self.view.undo.endMacro()

    def scale_selected(self):
        value, ok = QInputDialog.getDouble(self, "Auswahl skalieren", "Größe in Prozent", 100, 5, 2000, 1)
        if ok:
            self.view.scale_selection(value / 100)

    def closeEvent(self, event):
        held = QApplication.keyboardModifiers()
        if held & Qt.KeyboardModifier.AltModifier and held & Qt.KeyboardModifier.ShiftModifier:
            # Alt+Shift on the close button: close every editor without saving.
            self.discard_changes = True
            for window in [w for w in QApplication.topLevelWidgets() if isinstance(w, EditorWindow) and w is not self]:
                window.discard_changes = True
                window.close()
        if getattr(self, "discard_changes", False):
            event.accept()
            return
        if not self.view.undo.isClean():
            answer = QMessageBox.question(self, "Markierungen speichern?", "Die Bearbeitung wurde noch nicht als Projekt gespeichert.",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Save and not self.save_project():
                event.ignore()
                return
        event.accept()
