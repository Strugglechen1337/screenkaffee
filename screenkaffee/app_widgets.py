"""Listen-Widgets, Stylesheet, Hintergrund-Worker der Hauptoberfläche (aus app.py ausgelagert)."""
import logging
import json
import os
from pathlib import Path
import sys
import traceback
import threading
import numpy as np
from PIL import Image
from PySide6.QtCore import Qt, QSize, QTimer, QObject, QThread, Signal, Slot, QUrl, QRect, QPointF
from PySide6.QtGui import QIcon, QPixmap, QColor, QPainter, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QSpinBox, QLineEdit, QListWidget, QListWidgetItem,
    QAbstractItemView, QSplitter, QFileDialog, QMessageBox, QInputDialog,
    QSystemTrayIcon, QMenu, QDialog, QTextEdit, QDialogButtonBox, QCheckBox, QToolButton)
from .workspace import Workspace
from .color import import_srgb, save_png, save_image
from .editor import EditorWindow, pil_to_qimage
from .capture_options import DEFAULTS as CAPTURE_DEFAULTS, EXPORT_FORMATS as CAPTURE_FORMATS, post_process, title_for, validate as validate_capture_options
from .overlay import SelectionOverlay
from .hotkeys import DEFAULT_HOTKEYS, ACTIONS
from .integrations import tool, omarchy_capture, VideoRecorder, android_capture, AndroidRecorder


MEDIA_MIME = "application/x-screenkaffee-ids"
COLLECTION_ICONS = {"document": "Dokument", "camera": "Kamera", "edit": "Stift", "copy": "Kopie", "export": "Export",
                    "scan": "Rahmen", "video": "Video", "more": "Mehr"}


LOG = logging.getLogger(__name__)


class CollectionList(QListWidget):
    """Sidebar list; captures dragged from the grid can be dropped onto a collection to move them."""
    mediaDropped = Signal(list, str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MEDIA_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if event.mimeData().hasFormat(MEDIA_MIME) and item is not None and not item.text().startswith("+"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if item is None or item.text().startswith("+") or not event.mimeData().hasFormat(MEDIA_MIME):
            event.ignore()
            return
        ids = bytes(event.mimeData().data(MEDIA_MIME)).decode().split(",")
        self.mediaDropped.emit([i for i in ids if i], item.text())
        event.acceptProposedAction()


class CaptureList(QListWidget):
    """Drag exports contain flattened images by default; Shift+Drag exports the editable vector project (.screenkaffee.json)."""
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.setDragEnabled(True)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space and not event.modifiers():
            self.window.quick_view_selected()
        elif event.key() == Qt.Key.Key_F2:
            self.window.edit_details()
        elif event.key() == Qt.Key.Key_Escape:
            if not self.window.cancel_capture():
                self.window.search.clear()
        elif event.key() == Qt.Key.Key_F and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.window.search.setFocus()
            self.window.search.selectAll()
        elif event.text() and event.text().isprintable() and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            self.window.search.setFocus()
            self.window.search.setText(self.window.search.text() + event.text())
        else:
            super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        if item is not None and not item.isSelected():
            self.clearSelection()
            item.setSelected(True)
            self.setCurrentItem(item)
        self.window.show_capture_context_menu(self.mapToGlobal(event.pos()))

    def startDrag(self, supported_actions):
        from PySide6.QtCore import QMimeData
        from PySide6.QtGui import QDrag
        ids = self.window.selected_ids()
        if not ids:
            return
        shift_project = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        folder = self.window.workspace.root / "drag-exports"
        folder.mkdir(exist_ok=True)
        urls = []
        for ident in ids:
            row = self.window.workspace.get(ident)
            if row["kind"] == "image":
                if shift_project:
                    path = self.window.export_project_bundle(ident, folder)
                else:
                    path = folder / (ident + ".png")
                    save_png(self.window.rendered(ident), path)
            else:
                path = self.window.workspace.path(ident)
            urls.append(QUrl.fromLocalFile(str(path.resolve())))
        mime = QMimeData()
        mime.setUrls(urls)
        mime.setData(MEDIA_MIME, ",".join(ids).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


STYLE = """
QWidget { background: #2b2b2b; color: #ededed; font-family: 'Segoe UI', 'Noto Sans', sans-serif; font-size: 12px; }
QMainWindow, QDialog { background: #2b2b2b; }
QLabel { background: transparent; }
QWidget#topHeader { background: #2b2b2b; border-bottom: 1px solid #3f3f3f; }
QWidget#inspector { background: #333333; border-left: 1px solid #484848; border-radius: 0; }
QLabel#brand { font-size: 16px; font-weight: 700; color: #ffffff; }
QLabel#eyebrow { color: #f1a24a; font-weight: 700; font-size: 11px; letter-spacing: 2px; }
QLabel#muted { color: #b8b8b8; font-size: 11px; }
QWidget#commandGroup { background: #363636; border: 1px solid #505050; border-radius: 4px; }
QWidget#commandGroup QPushButton { background: transparent; border: 0; border-radius: 3px; padding: 5px 12px; font-size: 12px; }
QWidget#commandGroup QPushButton:hover { background: #484848; }
QWidget#commandGroup QPushButton#primary { background: #d83b2d; color: #ffffff; padding: 5px 14px; }
QWidget#commandGroup QPushButton#primary:hover { background: #e84c3d; }
QWidget#commandGroup QPushButton#menuArrow { padding: 5px 8px; border-left: 1px solid #4a4a4a; border-radius: 0; }
QPushButton#menuArrow::menu-indicator { image: none; width: 0; }
QPushButton { background: #3a3a3a; border: 1px solid #525252; border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background: #484848; border-color: #707070; }
QPushButton:pressed { background: #1e70bf; }
QPushButton:disabled { color: #6e6e6e; background: #252525; border-color: #383838; }
QPushButton#primary { background: #d83b2d; color: #ffffff; border: 0; }
QPushButton#primary:hover { background: #e84c3d; }
QPushButton::menu-indicator { subcontrol-origin: padding; subcontrol-position: right center; right: 8px; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit { background: #353535; border: 1px solid #525252; border-radius: 3px; padding: 4px 8px; selection-background-color: #1e70bf; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus { border-color: #3c8ce7; }
QComboBox::drop-down { border: 0; width: 22px; }
QComboBox::down-arrow { image: url(@ARROW@); width: 11px; height: 11px; margin-right: 6px; }
QComboBox QAbstractItemView { background: #363636; border: 1px solid #555555; selection-background-color: #1e70bf; padding: 4px; outline: 0; }
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 0; border: 0; }
QListWidget { background: #4c4c4c; border: 0; border-radius: 0; padding: 10px; outline: 0; }
QListWidget::item { padding: 6px 4px 4px 4px; border-radius: 4px; border: 1px solid transparent; color: #f0f0f0; font-size: 11px; }
QListWidget::item:selected { background: #1e70bf; border-color: #4aa3ff; color: white; }
QListWidget::item:hover:!selected { background: #585858; border-color: #6a6a6a; }
QToolBar { background: #333333; padding: 4px 6px; spacing: 3px; border: 0; }
QToolBar#tools { background: #2f2f2f; padding: 4px 3px; spacing: 1px; border-right: 1px solid #454545; }
QToolBar#tools QToolButton { min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px; padding: 3px; margin: 1px 2px; border-radius: 15px; background: #2c6da4; border: 1px solid #5294c8; }
QToolBar#tools QToolButton:hover { background: #3980bc; border-color: #7ab8ec; }
QToolBar#tools QToolButton:checked { background: #1e5a8c; border: 2px solid #ffaa3b; }
QToolBar#editorCommands { background: #333333; padding: 2px 3px; border-bottom: 1px solid #454545; spacing: 0px; }
QToolBar#editorCommands QToolButton { padding: 2px 3px; font-size: 10px; border-radius: 3px; color: #e8e8e8; margin: 0; }
QToolBar#editorCommands QToolButton#copyAndClose { background: #3b6e28; border: 1px solid #53923b; color: #ffffff; padding: 2px 6px; }
QToolBar#editorCommands QToolButton#copyAndClose:hover { background: #488432; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 4px; padding: 5px 8px; margin: 1px; }
QToolButton:hover { background: #484848; border-color: #5c5c5c; }
QToolButton:checked { background: #1e70bf; border-color: #4aa3ff; }
QToolButton:pressed { background: #195a99; }
QToolButton#headerIcon { background: transparent; border: 1px solid transparent; border-radius: 4px; padding: 4px 8px; color: #e0e0e0; font-size: 11px; }
QToolButton#headerIcon:hover { background: #404040; border-color: #555555; }
QToolButton#headerIcon:checked { background: #404040; border-color: #686868; }
QToolBar::separator { background: #4a4a4a; width: 1px; margin: 4px 4px; }
QStatusBar { background: #262626; color: #c0c0c0; border-top: 1px solid #3b3b3b; padding: 2px 6px; font-size: 11px; }
QStatusBar::item { border: 0; }
QMenu { background: #333333; border: 1px solid #555555; border-radius: 4px; padding: 4px; }
QMenu::item { padding: 6px 24px 6px 12px; border-radius: 3px; }
QMenu::item:selected { background: #1e70bf; color: white; }
QMenu::separator { height: 1px; background: #4a4a4a; margin: 4px 6px; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 15px; height: 15px; border-radius: 3px; border: 1px solid #5a5a5a; background: #333333; }
QCheckBox::indicator:checked { background: #1e70bf; border-color: #4aa3ff; }
QScrollBar:vertical { background: #3a3a3a; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #606060; border-radius: 4px; min-height: 30px; margin: 2px; }
QScrollBar::handle:vertical:hover { background: #787878; }
QScrollBar:horizontal { background: #3a3a3a; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: #606060; border-radius: 4px; min-width: 30px; margin: 2px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QSplitter::handle { background: #2b2b2b; width: 2px; }
QToolTip { background: #2b2b2b; color: #ededed; border: 1px solid #5a5a5a; padding: 4px 8px; }
QProgressBar { background: #333333; border: 0; border-radius: 5px; text-align: center; }
QProgressBar::chunk { background: #1e70bf; border-radius: 5px; }
QWidget#bottomBar { background: #333333; border-top: 1px solid #484848; border-radius: 0; }
QToolButton#roundAction { background: transparent; border: 0; border-radius: 6px; padding: 3px 6px; color: #e2e2e2; font-size: 11px; margin: 0; }
QToolButton#roundAction:hover { background: #444444; color: white; }
QToolButton#roundAction:pressed { background: #2a2a2a; }
QToolButton#roundAction::menu-indicator { image: none; width: 0; }
QListWidget#collections { background: #383838; border: 0; border-right: 1px solid #292929; border-radius: 0; padding: 6px 2px; }
QListWidget#collections::item { padding: 4px 2px; font-size: 11px; color: #dcdcdc; border-radius: 4px; border: 1px solid transparent; }
QListWidget#collections::item:selected { background: #505050; border-color: #686868; color: white; }
QListWidget#collections::item:hover:!selected { background: #444444; }
"""


def _arrow_url():
    """Draw the combo-box chevron once; Qt stylesheets can only reference image files."""
    import tempfile
    from PySide6.QtGui import QImage, QPen
    path = Path(tempfile.gettempdir()) / "screenkaffee-arrow-down.png"
    image = QImage(24, 24, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#b0b0b0"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.drawPolyline([QPointF(6, 9), QPointF(12, 15), QPointF(18, 9)])
    painter.end()
    image.save(str(path))
    return path.as_posix()


STYLE = STYLE.replace("@ARROW@", _arrow_url())


def app_icon():
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#e89a3c"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 16, 16)
    from PySide6.QtGui import QPen
    painter.setPen(QPen(QColor("#142534"), 5))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(15, 20, 34, 26, 5, 5)
    painter.drawEllipse(25, 26, 14, 14)
    painter.end()
    return QIcon(pix)


class Worker(QObject):
    done = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, operation):
        super().__init__()
        self.operation = operation

    def run(self):
        try:
            self.done.emit(self.operation())
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class TaskDelivery(QObject):
    """QObject receiver keeps every UI callback on the QApplication thread."""
    def __init__(self, window, callback):
        super().__init__(window)
        self.window, self.callback = window, callback

    @Slot(object)
    def success(self, result):
        self.callback(result)

    @Slot(str)
    def failure(self, message):
        self.window.fail(message)


class ThumbnailLoader(QObject):
    """Lädt Vorschauen schrittweise: pro Takt alle Cache-Treffer im Zeitbudget, aber höchstens ein Original dekodieren.

    Neue Listen ersetzen alte Aufträge; der Cache gehört immer zum aktuellen Arbeitsbereich des Besitzers."""
    BUDGET = 0.008            # Sekunden pro Takt, damit die Oberfläche flüssig bleibt

    def __init__(self, owner):
        super().__init__(owner)
        from collections import deque
        self.owner = owner
        self.cache = None
        self.bind()
        self.jobs = deque()
        self.timer = QTimer(self)
        self.timer.setInterval(0)
        self.timer.timeout.connect(self.load_next)

    def bind(self):
        """Cache an den aktuellen Arbeitsbereich binden (nach „Arbeitsbereich wechseln“ einen neuen anlegen)."""
        from .workspace import ThumbnailCache
        root = Path(self.owner.workspace.root)
        if self.cache is None or self.cache.root != root / "thumbnails":
            self.cache = ThumbnailCache(root)
        return self.cache

    def reset(self):
        self.timer.stop()
        self.jobs.clear()
        self.bind()

    def enqueue(self, item, source, kind, favorite=False):
        self.jobs.append((item, source, kind, favorite))
        self.timer.start()

    def load_next(self):
        import time
        from .icons import styled_thumbnail
        cache = self.bind()
        deadline = time.perf_counter() + self.BUDGET
        while self.jobs:
            item, source, kind, favorite = self.jobs.popleft()
            decoded = False
            try:
                path = cache.cached(source)
                if path is None:
                    decoded = True
                    path = cache.get(source)
                pixmap = QPixmap(str(path))
            except (OSError, ValueError):
                # Original fehlt oder ist kaputt: graue Platzhalterkarte mit Badge und Favoritenstern.
                LOG.debug("Vorschau konnte nicht geladen werden: %s", source, exc_info=True)
                pixmap = QPixmap()
            item.setIcon(styled_thumbnail(pixmap, kind, 172, 118, favorite=favorite))
            if decoded or time.perf_counter() >= deadline:
                break
        if not self.jobs:
            self.timer.stop()
