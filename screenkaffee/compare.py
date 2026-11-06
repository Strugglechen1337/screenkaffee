"""Zwei Aufnahmen vergleichen: Schieberegler, Überblenden und markierte Unterschiede."""
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw
from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QPainter, QPixmap, QColor, QPen
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSlider, QSpinBox, QPushButton

CELL = 16                    # Unterschiede werden in Zellen dieser Größe zu Bereichen zusammengefasst
DEFAULT_THRESHOLD = 24       # Farbabweichung je Kanal, die noch als gleich gilt (JPEG-Rauschen, Kantenglättung)
MODES = [("slider", "Schieberegler"), ("blend", "Überblenden"), ("diff", "Unterschiede markieren")]
MARK = (230, 40, 40)


@dataclass
class Difference:
    size: tuple
    ratio: float                                  # Anteil abweichender Pixel (0 … 1)
    regions: list = field(default_factory=list)   # (links, oben, rechts, unten) in Pixeln, größter Bereich zuerst
    mask: np.ndarray = None

    def summary(self):
        if not self.regions:
            return "Keine Unterschiede gefunden"
        percent = f"{self.ratio * 100:.2f}".replace(".", ",")
        count = len(self.regions)
        return f"{count} {'Bereich' if count == 1 else 'Bereiche'} verändert · {percent} % der Pixel"


def align(first, second):
    """Beide Bilder auf eine gemeinsame Fläche (oben links ausgerichtet); fehlende Flächen sind transparent."""
    width, height = max(first.width, second.width), max(first.height, second.height)
    result = []
    for image in (first, second):
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.paste(image.convert("RGBA"), (0, 0))
        result.append(canvas)
    return result


def changed_regions(mask, cell=CELL):
    """Abweichende Pixel zu Rechtecken zusammenfassen (benachbarte Zellen gehören zum selben Bereich)."""
    height, width = mask.shape
    rows, cols = -(-height // cell), -(-width // cell)
    padded = np.zeros((rows * cell, cols * cell), dtype=bool)
    padded[:height, :width] = mask
    grid = padded.reshape(rows, cell, cols, cell).any(axis=(1, 3))
    seen = np.zeros_like(grid)
    regions = []
    for row, col in zip(*np.nonzero(grid)):
        if seen[row, col]:
            continue
        stack, cells = [(row, col)], []
        seen[row, col] = True
        while stack:
            r, c = stack.pop()
            cells.append((r, c))
            for nr in range(max(0, r - 1), min(rows, r + 2)):
                for nc in range(max(0, c - 1), min(cols, c + 2)):
                    if grid[nr, nc] and not seen[nr, nc]:
                        seen[nr, nc] = True
                        stack.append((nr, nc))
        r0, r1 = min(r for r, _ in cells), max(r for r, _ in cells)
        c0, c1 = min(c for _, c in cells), max(c for _, c in cells)
        box = mask[r0 * cell:(r1 + 1) * cell, c0 * cell:(c1 + 1) * cell]
        ys, xs = np.nonzero(box)
        regions.append((int(c0 * cell + xs.min()), int(r0 * cell + ys.min()),
                        int(c0 * cell + xs.max() + 1), int(r0 * cell + ys.max() + 1)))
    regions.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    return regions


def difference(first, second, threshold=DEFAULT_THRESHOLD):
    a, b = (np.asarray(image, dtype=np.int16) for image in align(first, second))
    mask = np.abs(a - b).max(axis=2) > int(threshold)
    return Difference(size=(mask.shape[1], mask.shape[0]), ratio=float(mask.mean()) if mask.size else 0.0,
                      regions=changed_regions(mask), mask=mask)


def highlight(first, second, diff=None, threshold=DEFAULT_THRESHOLD):
    """Das neuere Bild, Unverändertes aufgehellt, Abweichungen rot getönt und umrandet."""
    diff = diff or difference(first, second, threshold)
    _, base = align(first, second)
    white = Image.new("RGBA", base.size, (255, 255, 255, 255))
    rgb = Image.alpha_composite(white, base).convert("RGB")
    pixels = np.asarray(rgb, dtype=np.float32)
    faded = pixels * 0.45 + 255 * 0.55
    tinted = pixels * 0.5 + np.array(MARK, dtype=np.float32) * 0.5
    out = np.where(diff.mask[..., None], tinted, faded).clip(0, 255).astype(np.uint8)
    image = Image.fromarray(out, "RGB")
    draw = ImageDraw.Draw(image)
    pad = 4
    for left, top, right, bottom in diff.regions:
        draw.rectangle((max(0, left - pad), max(0, top - pad), min(image.width - 1, right + pad - 1),
                        min(image.height - 1, bottom + pad - 1)), outline=MARK, width=3)
    return image


class CompareCanvas(QWidget):
    """Zeichnet beide Bilder eingepasst; im Schieberegler-Modus lässt sich die Trennlinie mit der Maus ziehen."""
    positionChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.mode = "slider"
        self.position = 50
        self.pixmaps = (QPixmap(), QPixmap(), QPixmap())       # vorher, nachher, Unterschiede
        self.setMinimumSize(480, 320)
        self.setMouseTracking(True)

    def target(self):
        source = self.pixmaps[1]
        if source.isNull():
            return QRectF()
        dpr = source.devicePixelRatio() or 1
        width, height = source.width() / dpr, source.height() / dpr
        scale = min(1.0, self.width() / width, self.height() / height)
        w, h = width * scale, height * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor("#2b2b2b"))
        rect = self.target()
        if rect.isEmpty():
            return
        before, after, marked = self.pixmaps
        if self.mode == "diff":
            painter.drawPixmap(rect, marked, QRectF(marked.rect()))
            return
        painter.drawPixmap(rect, after, QRectF(after.rect()))
        if self.mode == "blend":
            painter.setOpacity(1 - self.position / 100)
            painter.drawPixmap(rect, before, QRectF(before.rect()))
            return
        split = rect.left() + rect.width() * self.position / 100
        painter.save()
        painter.setClipRect(QRectF(rect.left(), rect.top(), split - rect.left(), rect.height()))
        painter.drawPixmap(rect, before, QRectF(before.rect()))
        painter.restore()
        painter.setPen(QPen(QColor("white"), 2))
        painter.drawLine(QPointF(split, rect.top()), QPointF(split, rect.bottom()))
        painter.setBrush(QColor("#e8912d"))
        painter.drawEllipse(QPointF(split, rect.center().y()), 9, 9)

    def mousePressEvent(self, event):
        self.drag(event.position())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.drag(event.position())

    def drag(self, point):
        rect = self.target()
        if self.mode != "slider" or rect.isEmpty():
            return
        self.positionChanged.emit(round(min(100, max(0, (point.x() - rect.left()) / rect.width() * 100))))


class CompareWindow(QWidget):
    """Eigenes Fenster: links/vorher gegen rechts/nachher, Toleranz einstellbar, Markierung als Aufnahme speichern."""

    def __init__(self, before, after, titles=("Vorher", "Nachher"), on_save=None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.before, self.after, self.titles, self.on_save = before, after, list(titles), on_save
        self.diff = None
        self.resize(1100, 760)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.mode = QComboBox()
        for value, label in MODES:
            self.mode.addItem(label, value)
        top.addWidget(QLabel("Ansicht"))
        top.addWidget(self.mode)
        top.addSpacing(16)
        top.addWidget(QLabel("Toleranz"))
        self.threshold = QSpinBox()
        self.threshold.setRange(0, 128)
        self.threshold.setValue(DEFAULT_THRESHOLD)
        self.threshold.setToolTip("Wie stark eine Farbe abweichen darf, bevor sie als Unterschied zählt")
        top.addWidget(self.threshold)
        top.addStretch()
        self.swap_button = QPushButton("Seiten tauschen")
        top.addWidget(self.swap_button)
        layout.addLayout(top)
        self.labels = QHBoxLayout()
        self.left_label, self.right_label = QLabel(), QLabel()
        self.right_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.labels.addWidget(self.left_label)
        self.labels.addWidget(self.right_label)
        layout.addLayout(self.labels)
        self.canvas = CompareCanvas()
        layout.addWidget(self.canvas, 1)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(50)
        layout.addWidget(self.slider)
        bottom = QHBoxLayout()
        self.summary = QLabel()
        bottom.addWidget(self.summary, 1)
        self.save_button = QPushButton("Markierung als Aufnahme speichern")
        self.save_button.setEnabled(on_save is not None)
        bottom.addWidget(self.save_button)
        close = QPushButton("Schließen")
        close.clicked.connect(self.close)
        bottom.addWidget(close)
        layout.addLayout(bottom)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.slider.valueChanged.connect(self.position_changed)
        self.canvas.positionChanged.connect(self.slider.setValue)
        self.threshold.valueChanged.connect(lambda _: self.recompute())
        self.swap_button.clicked.connect(self.swap)
        self.save_button.clicked.connect(self.save_marked)
        self.recompute()

    def pixmap(self, image):
        from .editor import pil_to_qimage
        pix = QPixmap.fromImage(pil_to_qimage(image.convert("RGBA")))
        pix.setDevicePixelRatio(self.devicePixelRatioF() or 1)
        return pix

    def recompute(self):
        self.diff = difference(self.before, self.after, self.threshold.value())
        self.marked = highlight(self.before, self.after, self.diff)
        size_note = ""
        if self.before.size != self.after.size:
            size_note = f" · Größe {self.before.width}×{self.before.height} → {self.after.width}×{self.after.height}"
        self.summary.setText(self.diff.summary() + size_note)
        self.setWindowTitle(f"Vergleichen: {self.titles[0]} ↔ {self.titles[1]}")
        self.left_label.setText("◀ " + self.titles[0])
        self.right_label.setText(self.titles[1] + " ▶")
        before, after = align(self.before, self.after)
        self.canvas.pixmaps = (self.pixmap(before), self.pixmap(after), self.pixmap(self.marked))
        self.canvas.update()

    def mode_changed(self):
        self.canvas.mode = self.mode.currentData()
        self.slider.setVisible(self.canvas.mode != "diff")
        self.canvas.update()

    def position_changed(self, value):
        self.canvas.position = value
        self.canvas.update()

    def swap(self):
        self.before, self.after = self.after, self.before
        self.titles.reverse()
        self.recompute()

    def save_marked(self):
        if self.on_save:
            self.on_save(self.marked)
            self.save_button.setText("Gespeichert ✓")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        elif event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.slider.setValue(self.slider.value() + (-5 if event.key() == Qt.Key.Key_Left else 5))
        else:
            super().keyPressEvent(event)


OPEN = []


def compare(before, after, titles=("Vorher", "Nachher"), on_save=None):
    window = CompareWindow(before, after, titles, on_save)
    OPEN.append(window)
    window.destroyed.connect(lambda *_: OPEN.remove(window) if window in OPEN else None)
    window.show()
    return window
