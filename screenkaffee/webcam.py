"""Movable camera overlay, rendered as regular pixels on both desktops."""
from .i18n import tr
from PySide6.QtCore import Qt, Slot, QPoint
from PySide6.QtGui import QPainter, QPainterPath, QColor, QImage
from PySide6.QtWidgets import QWidget, QApplication
from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QVideoSink


class WebcamOverlay(QWidget):
    def __init__(self, device, size=240, circle=True, mirror=False, position="bottom-right"):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(size, size)
        self.circle = circle
        self.mirror = mirror
        self.image = QImage()
        self.message = tr("Kamera startet …")
        self.drag_offset = None
        self.camera = QCamera(device, self)
        self.session = QMediaCaptureSession(self)
        self.sink = QVideoSink(self)
        self.session.setCamera(self.camera)
        self.session.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self.frame)
        self.camera.errorOccurred.connect(self.error)
        geometry = QApplication.primaryScreen().availableGeometry()
        self.place(position, geometry)

    def place(self, position, geometry):
        left = geometry.left() + 24 if position.endswith("left") else geometry.right() - self.width() - 24
        top = geometry.top() + 24 if position.startswith("top") else geometry.bottom() - self.height() - 24
        self.move(left, top)

    def start(self):
        self.show()
        self.camera.start()

    @Slot(object)
    def frame(self, frame):
        image = frame.toImage()
        if not image.isNull():
            self.image = image
            self.update()

    def error(self, error, message):
        self.message = tr("Kamerafehler: {message}", message=message)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        from PySide6.QtCore import QRectF
        if self.circle:
            path.addEllipse(QRectF(self.rect()))
        else:
            path.addRoundedRect(QRectF(self.rect()), 14, 14)
        painter.setClipPath(path)
        painter.fillRect(self.rect(), QColor("#172033"))
        if self.image.isNull():
            painter.setPen(QColor("white"))
            painter.drawText(self.rect().adjusted(15, 15, -15, -15), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
        else:
            source = (self.image.flipped(Qt.Orientation.Horizontal) if hasattr(self.image, "flipped") else self.image.mirrored(True, False)) if self.mirror else self.image
            image = source.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                      Qt.TransformationMode.SmoothTransformation)
            painter.drawImage(QPoint((self.width() - image.width()) // 2, (self.height() - image.height()) // 2), image)

    def mousePressEvent(self, event):
        self.drag_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self.drag_offset)

    def mouseReleaseEvent(self, event):
        self.drag_offset = None

    def closeEvent(self, event):
        self.camera.stop()
        self.session.setCamera(None)
        event.accept()
