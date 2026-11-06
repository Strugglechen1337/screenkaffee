"""Borderless, always-on-top preview of one image. Space, Escape or a right click closes it."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import QLabel


class QuickView(QLabel):
    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        primary = QGuiApplication.primaryScreen()
        screen = primary.availableGeometry()
        dpr = (1.0 if QGuiApplication.platformName() == "offscreen" else primary.devicePixelRatio()) or 1.0
        max_w, max_h = int(screen.width() * .9 * dpr), int(screen.height() * .9 * dpr)
        shown = pixmap.scaled(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation) if (
            pixmap.width() > max_w or pixmap.height() > max_h) else QPixmap(pixmap)
        shown.setDevicePixelRatio(dpr)
        self.setPixmap(shown)
        self.resize(round(shown.width() / dpr), round(shown.height() / dpr))
        self.move(screen.center() - self.rect().center())
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Escape):
            self.close()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.close()
        else:
            super().mousePressEvent(event)
