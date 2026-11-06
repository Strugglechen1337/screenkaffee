"""Angeheftete Aufnahme: rahmenloses Fenster, das immer im Vordergrund bleibt (z. B. zum Abtippen oder Vergleichen)."""
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from .editor_items import pil_to_qimage

ZOOM_MIN, ZOOM_MAX = 0.1, 4.0


class PinnedImage(QWidget):
    """Ziehen verschiebt, Mausrad zoomt, Doppelklick oder Esc schließt, Rechtsklick öffnet das Menü."""

    def __init__(self, image, title="", on_edit=None, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(("Angeheftet: " + title) if title else "Angeheftet")
        self.image = image
        self.pixmap = QPixmap.fromImage(pil_to_qimage(image))
        self.on_edit = on_edit
        self.drag_start = None
        self.zoom = 1.0
        screen = QGuiApplication.screenAt(self.cursor().pos()) or QGuiApplication.primaryScreen()
        self.dpr = screen.devicePixelRatio() if screen else 1.0
        if screen:          # große Aufnahmen passend verkleinern (höchstens 80 % des Bildschirms)
            area = screen.availableGeometry()
            logical_w, logical_h = image.width / self.dpr, image.height / self.dpr
            self.zoom = min(1.0, area.width() * 0.8 / logical_w, area.height() * 0.8 / logical_h)
        self.apply_zoom()
        self.setToolTip("Ziehen: verschieben · Mausrad: zoomen · Doppelklick/Esc: schließen · Rechtsklick: Menü")

    def logical_size(self):
        """Bei 100 % ein Bildpixel = ein Bildschirmpixel (wie im Editor)."""
        return (max(16, round(self.image.width / self.dpr * self.zoom)),
                max(16, round(self.image.height / self.dpr * self.zoom)))

    def apply_zoom(self, zoom=None, anchor=None):
        if zoom is not None:
            old = self.size()
            self.zoom = min(ZOOM_MAX, max(ZOOM_MIN, zoom))
            width, height = self.logical_size()
            if anchor is not None and old.width() and old.height():   # Punkt unter der Maus bleibt stehen
                dx = round(anchor.x() * (width / old.width() - 1))
                dy = round(anchor.y() * (height / old.height() - 1))
                self.move(self.pos() - QPoint(dx, dy))
        self.resize(*self.logical_size())
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom != 1.0)
        painter.drawPixmap(self.rect(), self.pixmap)
        painter.setPen(QPen(QColor("#e8912d"), 1))
        painter.drawRect(QRect(0, 0, self.width() - 1, self.height() - 1))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self.drag_start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_start)

    def mouseReleaseEvent(self, event):
        self.drag_start = None

    def mouseDoubleClickEvent(self, event):
        self.close()

    def wheelEvent(self, event):
        step = 1.1 if event.angleDelta().y() > 0 else 1 / 1.1
        self.apply_zoom(self.zoom * step, event.position().toPoint())

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close()
        elif key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.apply_zoom(self.zoom * 1.25)
        elif key == Qt.Key.Key_Minus:
            self.apply_zoom(self.zoom / 1.25)
        elif key == Qt.Key.Key_0:
            self.apply_zoom(1.0)
        elif key == Qt.Key.Key_C and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.copy()
        else:
            super().keyPressEvent(event)

    def copy(self):
        QApplication.clipboard().setImage(self.pixmap.toImage())

    def set_opacity(self, value):
        self.setWindowOpacity(value)

    def build_menu(self):
        menu = QMenu(self)
        menu.addAction("In Zwischenablage kopieren (Strg+C)", self.copy)
        if self.on_edit:
            menu.addAction("Im Editor öffnen", lambda: self.on_edit(self.image))
        opacity = menu.addMenu("Deckkraft")
        for percent in (100, 75, 50, 25):
            action = opacity.addAction(f"{percent} %", lambda value=percent / 100: self.set_opacity(value))
            action.setCheckable(True)
            action.setChecked(abs(self.windowOpacity() - percent / 100) < 0.01)
        menu.addAction("Originalgröße (0)", lambda: self.apply_zoom(1.0))
        menu.addSeparator()
        menu.addAction("Schließen (Esc)", self.close)
        return menu

    def contextMenuEvent(self, event):
        self.build_menu().exec(event.globalPos())


OPEN = []           # hält angeheftete Fenster am Leben, auch wenn der Editor geschlossen wird


def pin(image, title="", on_edit=None):
    window = PinnedImage(image, title, on_edit)
    OPEN.append(window)
    window.destroyed.connect(lambda *_: OPEN.remove(window) if window in OPEN else None)
    cursor = window.cursor().pos()
    window.move(cursor - QPoint(window.width() // 2, window.height() // 2))
    window.show()
    window.raise_()
    return window
