"""Undo-Befehle des Editors."""
import logging
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
from .editor_items import *  # noqa: F401,F403


class AddCommand(QUndoCommand):
    def __init__(self, view, item):
        super().__init__("Markierung hinzufügen")
        self.view, self.item = view, item

    def redo(self):
        if not self.item.scene():
            self.view.scene().addItem(self.item)

    def undo(self):
        self.view.scene().removeItem(self.item)


class DeleteCommand(QUndoCommand):
    def __init__(self, view, items):
        super().__init__("Markierung entfernen")
        self.view, self.items = view, items

    def redo(self):
        for item in self.items:
            self.view.scene().removeItem(item)

    def undo(self):
        for item in self.items:
            self.view.scene().addItem(item)


class CropCommand(QUndoCommand):
    def __init__(self, view, after):
        super().__init__("Zuschneiden")
        self.view, self.before, self.after = view, QRectF(view.crop_rect), QRectF(after)

    def redo(self):
        self.view.set_crop(self.after)

    def undo(self):
        self.view.set_crop(self.before)


class EffectsCommand(QUndoCommand):
    def __init__(self, view, after):
        super().__init__("Bildeffekte ändern")
        self.view, self.before, self.after = view, dict(view.effects), dict(after)
        self.geometry_changed = any(self.before.get(k) != self.after.get(k) for k in ("rotation", "flip_horizontal", "flip_vertical"))

    def redo(self):
        self.view.effects = dict(self.after)
        self.view.refresh_effects_preview(self.geometry_changed)

    def undo(self):
        self.view.effects = dict(self.before)
        self.view.refresh_effects_preview(self.geometry_changed)


class SourceCommand(QUndoCommand):
    def __init__(self, view, image):
        super().__init__("Hauptbild ersetzen")
        self.view = view
        self.before = (view.source.copy(), QRectF(view.crop_rect), view.source_changed)
        self.after = (image.convert("RGBA" if "A" in image.getbands() else "RGB"), QRectF(0, 0, image.width, image.height), True)

    def apply(self, state):
        image, crop, changed = state
        self.view.source = image.copy()
        self.view._base_key = None
        self.view.source_changed = changed
        self.view.set_crop(crop)
        self.view.refresh_effects_preview(False)

    def redo(self):
        self.apply(self.after)

    def undo(self):
        self.apply(self.before)


class CutOutCommand(SourceCommand):
    """Remove a strip from the main image; annotations behind the strip move up/left."""
    def __init__(self, view, image, axis, start, end):
        super().__init__(view, image)
        self.setText("Bereich ausschneiden")
        shift = (view.source.height - image.height) if axis == "y" else (view.source.width - image.width)
        self.moves = []
        for item in view.annotations():
            x1, y1, x2, y2 = item.data["points"]
            reference = item.pos().y() + min(y1, y2) if axis == "y" else item.pos().x() + min(x1, x2)
            if reference >= end:
                before = item.pos()
                after = QPointF(before.x(), before.y() - shift) if axis == "y" else QPointF(before.x() - shift, before.y())
                self.moves.append((item, before, after))

    def redo(self):
        super().redo()
        for item, _, after in self.moves:
            item.setPos(after)

    def undo(self):
        super().undo()
        for item, before, _ in self.moves:
            item.setPos(before)
