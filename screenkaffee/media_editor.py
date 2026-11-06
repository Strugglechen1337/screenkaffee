from pathlib import Path
from PIL import Image
from PySide6.QtCore import Qt, QPointF, QRectF, QUrl, QThread, Signal, Slot
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QDoubleSpinBox, QSpinBox, QCheckBox, QFileDialog, QSlider, QFormLayout, QMessageBox,
    QDialog, QDialogButtonBox, QTableWidget, QTableWidgetItem, QInputDialog, QComboBox, QListWidget)
from .media import probe_media, edit_video, merge_videos, extract_frame, export_html_page, VIDEO_SIZES

CROP_RATIOS = [("Frei", None), ("16:9", (16, 9)), ("4:3", (4, 3)), ("1:1", (1, 1)), ("9:16", (9, 16))]
CORNER_HANDLES = ("nw", "ne", "sw", "se")


def even_value(value, minimum=0):
    """Gerade Pixelzahl für H.264-Kompatibilität."""
    return max(minimum, int(round(value)) // 2 * 2)


def crop_corners(rect):
    x, y, w, h = rect
    return {"nw": (x, y), "ne": (x + w, y), "sw": (x, y + h), "se": (x + w, y + h)}


def crop_hit_test(rect, point, margin=10):
    """Eckgriff ('nw', 'ne', 'sw', 'se') unter dem Punkt oder None."""
    for name, (hx, hy) in crop_corners(rect).items():
        if abs(point[0] - hx) <= margin and abs(point[1] - hy) <= margin:
            return name
    return None


def constrain_crop(rect, bounds, ratio=None, min_size=2):
    """Begrenzt (x, y, w, h) auf `bounds` (Breite, Höhe), rundet alle Werte gerade (H.264) und wendet optional `ratio` an."""
    max_bw = max(min_size, int(bounds[0]) // 2 * 2)
    max_bh = max(min_size, int(bounds[1]) // 2 * 2)
    x = max(0, min(max_bw - min_size, even_value(rect[0], 0)))
    y = max(0, min(max_bh - min_size, even_value(rect[1], 0)))
    avail_w = max(min_size, max_bw - x)
    avail_h = max(min_size, max_bh - y)
    w = max(min_size, min(avail_w, even_value(rect[2], min_size)))
    h = max(min_size, min(avail_h, even_value(rect[3], min_size)))
    if ratio is not None:
        rw, rh = ratio
        scale = min(w / rw, h / rh, avail_w / rw, avail_h / rh)
        max_k = int(min(avail_w // (2 * rw), avail_h // (2 * rh)))
        if max_k >= 1:
            k = max(1, min(max_k, int(round(scale / 2))))
            w, h = 2 * k * rw, 2 * k * rh
        else:
            full_k = int(min(max_bw // (2 * rw), max_bh // (2 * rh)))
            if full_k >= 1:
                k = max(1, min(full_k, int(round(min(w / rw, h / rh) / 2))))
                w, h = 2 * k * rw, 2 * k * rh
            else:
                scale = min(max_bw / rw, max_bh / rh)
                w = max(min_size, int(round(scale * rw)) // 2 * 2)
                h = max(min_size, int(round(scale * rh)) // 2 * 2)
            x = max(0, min(x, (max_bw - w) // 2 * 2))
            y = max(0, min(y, (max_bh - h) // 2 * 2))
    return (int(x), int(y), int(w), int(h))


def drag_crop_rect(anchor, target, bounds, ratio=None, mode="new", min_size=2):
    """Berechnet (x, y, w, h) beim Aufziehen oder Ziehen eines Eckgriffs von `anchor` nach `target`."""
    max_bw = max(min_size, int(bounds[0]) // 2 * 2)
    max_bh = max(min_size, int(bounds[1]) // 2 * 2)
    ax = max(0, min(max_bw, even_value(anchor[0], 0)))
    ay = max(0, min(max_bh, even_value(anchor[1], 0)))
    tx = max(0, min(max_bw, float(target[0])))
    ty = max(0, min(max_bh, float(target[1])))
    if mode == "se":
        sx, sy = 1, 1
    elif mode == "nw":
        sx, sy = -1, -1
    elif mode == "ne":
        sx, sy = 1, -1
    elif mode == "sw":
        sx, sy = -1, 1
    else:
        sx = 1 if tx >= ax else -1
        sy = 1 if ty >= ay else -1
    avail_w = max(min_size, ((max_bw - ax) if sx > 0 else ax) // 2 * 2)
    avail_h = max(min_size, ((max_bh - ay) if sy > 0 else ay) // 2 * 2)
    raw_w = max(min_size, min(avail_w, sx * (tx - ax)))
    raw_h = max(min_size, min(avail_h, sy * (ty - ay)))
    if ratio is None:
        w = max(min_size, min(avail_w, even_value(raw_w, min_size)))
        h = max(min_size, min(avail_h, even_value(raw_h, min_size)))
    else:
        rw, rh = ratio
        scale = min(avail_w / rw, avail_h / rh, max(raw_w / rw, raw_h / rh))
        max_k = int(min(avail_w // (2 * rw), avail_h // (2 * rh)))
        if max_k >= 1:
            k = max(1, min(max_k, int(round(scale / 2))))
            w, h = 2 * k * rw, 2 * k * rh
        else:
            w = max(min_size, min(avail_w, even_value(scale * rw, min_size)))
            h = max(min_size, min(avail_h, even_value(scale * rh, min_size)))
    left = ax if sx > 0 else max(0, ax - w)
    top = ay if sy > 0 else max(0, ay - h)
    return constrain_crop((left, top, w, h), bounds, ratio=ratio, min_size=min_size)


class VideoCropWidget(QWidget):
    """Interaktiver Zuschnittsrahmen mit Eckgriffen über einem Standbild der aktuellen Videoposition."""
    cropChanged = Signal(tuple)

    def __init__(self, width, height, parent=None):
        super().__init__(parent)
        self.video_size = (even_value(width, 2), even_value(height, 2))
        self.ratio = None
        self.crop_rect = (0, 0, self.video_size[0], self.video_size[1])
        self.pixmap = QPixmap()
        self._drag = None
        self.setMinimumSize(220, 140)
        self.setMouseTracking(True)

    def set_frame(self, image):
        if isinstance(image, Image.Image):
            rgb = image.convert("RGB")
            qimg = QImage(rgb.tobytes("raw", "RGB"), rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888).copy()
            self.pixmap = QPixmap.fromImage(qimg)
        elif isinstance(image, QImage):
            self.pixmap = QPixmap.fromImage(image)
        elif isinstance(image, QPixmap):
            self.pixmap = image
        self.update()

    def image_rect(self):
        pad = 8.0
        avail_w = max(20.0, self.width() - 2 * pad)
        avail_h = max(20.0, self.height() - 2 * pad)
        vw, vh = self.video_size
        scale = min(avail_w / max(1, vw), avail_h / max(1, vh))
        dw, dh = vw * scale, vh * scale
        return QRectF((self.width() - dw) / 2, (self.height() - dh) / 2, dw, dh)

    def video_to_widget(self, vx, vy):
        rect = self.image_rect()
        vw, vh = self.video_size
        return QPointF(rect.left() + (vx / max(1, vw)) * rect.width(),
                       rect.top() + (vy / max(1, vh)) * rect.height())

    def widget_to_video(self, point):
        rect = self.image_rect()
        vw, vh = self.video_size
        px = point.x() if hasattr(point, "x") else point[0]
        py = point.y() if hasattr(point, "y") else point[1]
        vx = (px - rect.left()) / max(1.0, rect.width()) * vw
        vy = (py - rect.top()) / max(1.0, rect.height()) * vh
        return (max(0.0, min(float(vw), vx)), max(0.0, min(float(vh), vy)))

    def view_crop_rect(self):
        x, y, w, h = self.crop_rect
        tl = self.video_to_widget(x, y)
        br = self.video_to_widget(x + w, y + h)
        return QRectF(tl, br)

    def corner_handles(self):
        x, y, w, h = self.crop_rect
        return {name: self.video_to_widget(cx, cy) for name, (cx, cy) in crop_corners((x, y, w, h)).items()}

    def set_crop(self, rect, emit=False):
        self.crop_rect = constrain_crop(rect, self.video_size, self.ratio)
        self.update()
        if emit:
            self.cropChanged.emit(self.crop_rect)
        return self.crop_rect

    def set_ratio(self, ratio, emit=True):
        self.ratio = ratio
        return self.set_crop(self.crop_rect, emit=emit)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#1f1f1f"))
        img_rect = self.image_rect()
        if not self.pixmap.isNull():
            painter.drawPixmap(img_rect.toRect(), self.pixmap)
        else:
            painter.fillRect(img_rect, QColor("#353535"))
        vr = self.view_crop_rect()
        overlay = QPainterPath()
        overlay.addRect(img_rect)
        overlay.addRect(vr)
        overlay.setFillRule(Qt.FillRule.OddEvenFill)
        painter.fillPath(overlay, QColor(0, 0, 0, 130))
        painter.setPen(QPen(QColor("#e5332a"), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(vr)
        painter.setBrush(QColor("#ffffff"))
        for pt in self.corner_handles().values():
            painter.drawRect(QRectF(pt.x() - 4, pt.y() - 4, 8, 8))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        vr = self.view_crop_rect()
        hit = crop_hit_test((vr.x(), vr.y(), vr.width(), vr.height()), (pos.x(), pos.y()), margin=10)
        if hit in CORNER_HANDLES:
            x, y, w, h = self.crop_rect
            opp = {"nw": (x + w, y + h), "ne": (x, y + h), "sw": (x + w, y), "se": (x, y)}[hit]
            self._drag = (hit, opp)
        else:
            vx, vy = self.widget_to_video(pos)
            self._drag = ("new", (even_value(vx, 0), even_value(vy, 0)))

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._drag is None:
            vr = self.view_crop_rect()
            hit = crop_hit_test((vr.x(), vr.y(), vr.width(), vr.height()), (pos.x(), pos.y()), margin=10)
            cursors = {"nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
                       "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor}
            self.setCursor(cursors.get(hit, Qt.CursorShape.CrossCursor))
            return
        mode, anchor = self._drag
        target = self.widget_to_video(pos)
        if mode == "new" and abs(target[0] - anchor[0]) < 2 and abs(target[1] - anchor[1]) < 2:
            return
        self.set_crop(drag_crop_rect(anchor, target, self.video_size, self.ratio, mode=mode), emit=True)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = None


class MediaJob(QThread):
    result = Signal(object)
    failure = Signal(str)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.result.emit(self.operation())
        except Exception as exc:
            self.failure.emit(str(exc))


class VideoEditor(QMainWindow):
    videoSaved = Signal(str)
    frameSaved = Signal(object)

    def __init__(self, path, parent=None, animation_fps=15, animation_loops=0):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.animation_loops = animation_loops
        self.path = Path(path)
        self.info = probe_media(self.path)
        self.setWindowTitle(self.path.name + " — Video · screenkaffee")
        self.resize(1120, 820)
        self.job = None
        self.captions = []
        self.captions_dirty = False
        self.cuts = []
        self._syncing_crop = False
        root = QWidget()
        layout = QVBoxLayout(root)
        from .linux_audio_guard import prepare_audio
        prepare_audio()
        self.video = QVideoWidget()
        layout.addWidget(self.video, 1)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.setSource(QUrl.fromLocalFile(str(self.path.resolve())))
        controls = QHBoxLayout()
        play = QPushButton("Wiedergabe / Pause")
        play.clicked.connect(self.toggle_play)
        controls.addWidget(play)
        self.position = QSlider(Qt.Orientation.Horizontal)
        self.position.setRange(0, round(self.info.duration * 1000))
        self.position.sliderMoved.connect(self.player.setPosition)
        self.player.positionChanged.connect(self.position.setValue)
        self.player.positionChanged.connect(self.caption_position)
        controls.addWidget(self.position, 1)
        frame = QPushButton("Aktuelles Bild übernehmen")
        frame.clicked.connect(self.extract_current_frame)
        controls.addWidget(frame)
        layout.addLayout(controls)
        self.caption_label = QLabel()
        self.caption_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption_label.setWordWrap(True)
        layout.addWidget(self.caption_label)
        self.show_captions = QCheckBox("Untertitel anzeigen")
        self.show_captions.setChecked(True)
        self.show_captions.toggled.connect(lambda: self.caption_position(self.player.position()))
        layout.addWidget(self.show_captions)
        options = QHBoxLayout()
        form = QFormLayout()
        self.start = QDoubleSpinBox()
        self.end = QDoubleSpinBox()
        for widget in (self.start, self.end):
            widget.setRange(0, max(1, self.info.duration))
            widget.setDecimals(3)
            widget.setSuffix(" s")
        self.end.setValue(self.info.duration)
        form.addRow("Beginn", self.start)
        form.addRow("Ende", self.end)
        start_button = QPushButton("Aktuelle Position als Beginn")
        start_button.clicked.connect(lambda: self.start.setValue(self.player.position() / 1000))
        form.addRow(start_button)
        end_button = QPushButton("Aktuelle Position als Ende")
        end_button.clicked.connect(lambda: self.end.setValue(self.player.position() / 1000))
        form.addRow(end_button)
        self.cut_start = QDoubleSpinBox()
        self.cut_end = QDoubleSpinBox()
        for widget in (self.cut_start, self.cut_end):
            widget.setRange(0, max(1, self.info.duration))
            widget.setDecimals(3)
            widget.setSuffix(" s")
        form.addRow("Schnitt-Beginn", self.cut_start)
        form.addRow("Schnitt-Ende", self.cut_end)
        self.cut_start_button = QPushButton("Aktuelle Position als Schnitt-Beginn")
        self.cut_start_button.clicked.connect(lambda: self.set_cut_start())
        form.addRow(self.cut_start_button)
        self.cut_end_button = QPushButton("Aktuelle Position als Schnitt-Ende")
        self.cut_end_button.clicked.connect(lambda: self.set_cut_end())
        form.addRow(self.cut_end_button)
        cut_buttons = QHBoxLayout()
        self.add_cut_button = QPushButton("Schnitt hinzufügen")
        self.add_cut_button.clicked.connect(self._on_add_cut_clicked)
        cut_buttons.addWidget(self.add_cut_button)
        self.remove_cut_button = QPushButton("Schnitt entfernen")
        self.remove_cut_button.clicked.connect(lambda: self.remove_cut())
        cut_buttons.addWidget(self.remove_cut_button)
        form.addRow(cut_buttons)
        self.cut_list = QListWidget()
        self.cut_list.setMaximumHeight(72)
        form.addRow("Entfernte Abschnitte", self.cut_list)
        options.addLayout(form)
        form2 = QFormLayout()
        self.speed = QDoubleSpinBox()
        self.speed.setRange(.125, 8)
        self.speed.setDecimals(3)
        self.speed.setSingleStep(.25)
        self.speed.setValue(1)
        self.speed.valueChanged.connect(self.player.setPlaybackRate)
        form2.addRow("Geschwindigkeit", self.speed)
        self.volume = QDoubleSpinBox()
        self.volume.setRange(0, 4)
        self.volume.setSingleStep(.1)
        self.volume.setValue(1)
        self.volume.valueChanged.connect(lambda value: self.audio.setVolume(min(1, value)))
        form2.addRow("Lautstärke", self.volume)
        self.mute = QCheckBox("Ton entfernen")
        self.mute.toggled.connect(self.audio.setMuted)
        form2.addRow(self.mute)
        self.width = QSpinBox()
        self.width.setRange(16, 16384)
        self.width.setValue(self.info.width)
        form2.addRow("Ausgabebreite", self.width)
        self.size_box = QComboBox()
        for label, value in VIDEO_SIZES:
            self.size_box.addItem(label, value)
        form2.addRow("Ausgabegröße", self.size_box)
        self.resize_mode = QComboBox()
        for label, value in [("Einpassen (Fit Max)", "fitmax"), ("Nie vergrößern (Max)", "max"), ("Exakt mit Rändern (Exact)", "exact")]:
            self.resize_mode.addItem(label, value)
        form2.addRow("Skalierung", self.resize_mode)
        self.quality = QComboBox()
        for label, value in [("Niedrig (klein)", "low"), ("Mittel", "medium"), ("Hoch", "high")]:
            self.quality.addItem(label, value)
        self.quality.setCurrentIndex(1)
        form2.addRow("Qualität", self.quality)
        self.fps = QSpinBox()
        self.fps.setRange(1, 60)
        self.fps.setValue(animation_fps)
        self.fps.setToolTip("Bilder pro Sekunde für GIF, animiertes PNG und animiertes WebP")
        form2.addRow("Animation: Bilder/s", self.fps)
        options.addLayout(form2)
        form3 = QFormLayout()
        self.crop_enabled = QCheckBox("Bild zuschneiden")
        form3.addRow(self.crop_enabled)
        self.crop_ratio = QComboBox()
        for label, value in CROP_RATIOS:
            self.crop_ratio.addItem(label, value)
        form3.addRow("Seitenverhältnis", self.crop_ratio)
        self.crop = []
        even_w, even_h = even_value(self.info.width, 2), even_value(self.info.height, 2)
        for name, default, maximum in [("X", 0, even_w), ("Y", 0, even_h),
            ("Breite", even_w, even_w), ("Höhe", even_h, even_h)]:
            widget = QSpinBox()
            widget.setRange(0, maximum)
            widget.setSingleStep(2)
            widget.setValue(default)
            widget.valueChanged.connect(self._on_crop_spinbox_changed)
            self.crop.append(widget)
            form3.addRow(name, widget)
        self.crop_widget = VideoCropWidget(self.info.width, self.info.height, self)
        self.crop_widget.cropChanged.connect(self._on_crop_widget_changed)
        self.video.videoSink().videoFrameChanged.connect(self._crop_video_frame)
        self.crop_ratio.currentIndexChanged.connect(lambda: self.crop_widget.set_ratio(self.crop_ratio.currentData(), emit=True))
        form3.addRow(self.crop_widget)
        self.refresh_crop_frame_button = QPushButton("Standbild an aktueller Position")
        self.refresh_crop_frame_button.clicked.connect(lambda: self.update_crop_frame())
        form3.addRow(self.refresh_crop_frame_button)
        options.addLayout(form3)
        layout.addLayout(options)
        self.update_crop_frame(0)
        actions = QHBoxLayout()
        self.apply_button = QPushButton("Bearbeitetes Video exportieren …")
        self.apply_button.setObjectName("primary")
        self.apply_button.clicked.connect(self.export_video)
        actions.addWidget(self.apply_button)
        merge = QPushButton("Weitere Videos anhängen …")
        merge.clicked.connect(self.merge)
        actions.addWidget(merge)
        self.status = QLabel(f"{self.info.width} × {self.info.height} · {self.info.duration:.2f} s · {self.info.fps:.2f} FPS")
        actions.addWidget(self.status, 1)
        layout.addLayout(actions)
        subtitles = QHBoxLayout()
        for title, callback in [("Untertitel lokal erzeugen", self.generate_subtitles),
            ("SRT öffnen …", self.load_subtitles), ("Untertitel bearbeiten …", self.edit_subtitles),
            ("SRT / Transkript speichern …", self.save_subtitles), ("Untertitel in MP4 einbetten …", self.embed_subtitles)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            subtitles.addWidget(button)
        layout.addLayout(subtitles)
        self.setCentralWidget(root)
        self.player.errorOccurred.connect(lambda error, message: self.status.setText(message))

    def set_cut_start(self, seconds=None):
        self.cut_start.setValue(self.player.position() / 1000 if seconds is None else float(seconds))

    def set_cut_end(self, seconds=None):
        self.cut_end.setValue(self.player.position() / 1000 if seconds is None else float(seconds))

    def add_cut(self, start=None, end=None):
        start = self.cut_start.value() if start is None else float(start)
        end = self.cut_end.value() if end is None else float(end)
        if start < 0 or end <= start or (self.info.duration > 0 and end > self.info.duration + .1):
            raise ValueError("Ungültiger Schnittbereich: Ende muss nach Beginn liegen.")
        self.cuts.append((start, end))
        self._refresh_cut_list()
        return (start, end)

    def _on_add_cut_clicked(self):
        try:
            self.add_cut()
        except ValueError as exc:
            self.status.setText(str(exc))

    def remove_cut(self, index=None):
        if index is None:
            index = self.cut_list.currentRow()
        if 0 <= index < len(self.cuts):
            removed = self.cuts.pop(index)
            self._refresh_cut_list()
            return removed
        return None

    def _refresh_cut_list(self):
        self.cut_list.clear()
        for start, end in self.cuts:
            self.cut_list.addItem(f"{start:.3f} s – {end:.3f} s")

    def _on_crop_widget_changed(self, rect):
        self._syncing_crop = True
        try:
            for widget, value in zip(self.crop, rect):
                widget.setValue(value)
            self.crop_enabled.setChecked(True)
        finally:
            self._syncing_crop = False

    def _on_crop_spinbox_changed(self, *_):
        if self._syncing_crop:
            return
        raw = tuple(w.value() for w in self.crop)
        constrained = self.crop_widget.set_crop(raw, emit=False)
        if constrained != raw:
            self._syncing_crop = True
            try:
                for widget, value in zip(self.crop, constrained):
                    widget.setValue(value)
            finally:
                self._syncing_crop = False

    def _crop_video_frame(self, frame):
        """Standbild beim Abspielen und beim Springen synchron halten."""
        if frame.isValid():
            self.crop_widget.set_frame(frame.toImage())

    def update_crop_frame(self, seconds=None):
        if seconds is None:
            seconds = self.player.position() / 1000
        try:
            frame = extract_frame(self.path, max(0.0, min(max(0.0, self.info.duration - 0.05), float(seconds))))
        except Exception:
            frame = Image.new("RGB", (max(2, self.info.width), max(2, self.info.height)), "#2d2d2d")
        self.crop_widget.set_frame(frame)
        return frame

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def submit(self, operation, kind="video"):
        if self.job:
            return
        self.player.pause()
        self.job_kind = kind
        self.job = MediaJob(operation, self)
        self.job.result.connect(self.operation_done, Qt.ConnectionType.QueuedConnection)
        self.job.failure.connect(self.operation_failed, Qt.ConnectionType.QueuedConnection)
        self.job.finished.connect(self.job_finished)
        self.job.finished.connect(self.job.deleteLater)
        self.apply_button.setEnabled(False)
        self.status.setText("Verarbeitung läuft …")
        self.job.start()

    @Slot(object)
    def operation_done(self, result):
        if self.job_kind == "frame":
            self.frameSaved.emit(result)
            self.status.setText("Videobild übernommen")
        elif self.job_kind == "subtitles":
            self.captions = result
            self.captions_dirty = True
            self.status.setText(f"{len(result)} Untertitel lokal erzeugt")
            self.edit_subtitles()
        else:
            self.videoSaved.emit(str(result))
            self.status.setText("Video gespeichert: " + str(result))

    @Slot(str)
    def operation_failed(self, message):
        self.status.setText("Verarbeitung fehlgeschlagen; Original bleibt erhalten")
        QMessageBox.warning(self, "Video konnte nicht verarbeitet werden", message)

    @Slot()
    def job_finished(self):
        self.job = None
        self.apply_button.setEnabled(True)

    def extract_current_frame(self):
        seconds = self.player.position() / 1000
        self.submit(lambda: extract_frame(self.path, seconds), "frame")

    def export_video(self):
        path, _ = QFileDialog.getSaveFileName(self, "Video exportieren", str(self.path.with_name(self.path.stem + "-bearbeitet.mp4")),
            "MP4 (*.mp4);;MKV (AV1) (*.mkv);;WebM (*.webm);;MOV (*.mov);;AVI (*.avi);;WMV (*.wmv);;OGV (*.ogv);;"
            "GIF (*.gif);;Animiertes PNG (*.apng);;Animiertes WebP (*.webp);;HTML5-Webseite (*.html)")
        if not path:
            return
        options = {"start": self.start.value(), "end": self.end.value(), "speed": self.speed.value(),
            "mute": self.mute.isChecked(), "volume": self.volume.value(), "width": self.width.value(),
            "crop": tuple(w.value() for w in self.crop) if self.crop_enabled.isChecked() else None,
            "size": self.size_box.currentData(), "resize_mode": self.resize_mode.currentData(),
            "quality": self.quality.currentData(), "fps": self.fps.value(), "loops": self.animation_loops,
            "cuts": list(self.cuts)}
        if path.lower().endswith(".html"):
            self.submit(lambda: export_html_page(self.path, path, **options))
        else:
            self.submit(lambda: edit_video(self.path, path, **options))

    def merge(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Videos in Reihenfolge anhängen", "", "Videos (*.mp4 *.webm *.mov *.mkv *.avi)")
        if not paths:
            return
        destination, _ = QFileDialog.getSaveFileName(self, "Zusammengeführtes Video", str(self.path.with_name(self.path.stem + "-kombiniert.mp4")), "MP4 (*.mp4)")
        if destination:
            self.submit(lambda: merge_videos([self.path, *paths], destination))

    def generate_subtitles(self):
        prepare = getattr(self, "prepare_speech", None)          # vom Arbeitsbereich gesetzt: lädt die Erweiterung bei Bedarf
        if prepare is not None and not prepare(self, self.generate_subtitles):
            return
        from .subtitles import transcribe
        language, ok = QInputDialog.getItem(self, "Untertitelsprache", "Gesprochene Sprache", ["Automatisch", "de", "en", "fr", "es", "it", "nl"], editable=True)
        if ok:
            self.submit(lambda: transcribe(self.path, None if language == "Automatisch" else language), "subtitles")

    def load_subtitles(self):
        path, _ = QFileDialog.getOpenFileName(self, "Untertitel öffnen", "", "SRT (*.srt)")
        if path:
            try:
                from .subtitles import read_srt
                self.captions = read_srt(path)
                self.captions_dirty = False
                self.edit_subtitles()
            except Exception as exc:
                self.operation_failed(str(exc))

    def edit_subtitles(self):
        from .subtitles import Subtitle, validate
        dialog = QDialog(self)
        dialog.setWindowTitle("Untertitel: Text und Zeit")
        dialog.resize(850, 550)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(len(self.captions), 3)
        table.setHorizontalHeaderLabels(["Beginn (s)", "Ende (s)", "Text"])
        table.horizontalHeader().setStretchLastSection(True)
        for row, caption in enumerate(self.captions):
            for column, value in enumerate([f"{caption.start:.3f}", f"{caption.end:.3f}", caption.text]):
                table.setItem(row, column, QTableWidgetItem(value))
        layout.addWidget(table)
        actions = QHBoxLayout()
        add = QPushButton("Zeile hinzufügen")
        def add_row():
            row = table.rowCount()
            table.insertRow(row)
            start = self.player.position() / 1000
            for col, value in enumerate([f"{start:.3f}", f"{start + 2:.3f}", ""]):
                table.setItem(row, col, QTableWidgetItem(value))
        add.clicked.connect(add_row)
        actions.addWidget(add)
        remove = QPushButton("Gewählte Zeile löschen")
        remove.clicked.connect(lambda: table.removeRow(table.currentRow()) if table.currentRow() >= 0 else None)
        actions.addWidget(remove)
        jump = QPushButton("Zur gewählten Zeit springen")
        def jump_to_row():
            row = table.currentRow()
            if row >= 0:
                try:
                    self.player.setPosition(round(float(table.item(row, 0).text()) * 1000))
                except (ValueError, AttributeError):
                    pass
        jump.clicked.connect(jump_to_row)
        actions.addWidget(jump)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        def accept():
            try:
                result = []
                for row in range(table.rowCount()):
                    values = [table.item(row, col).text() if table.item(row, col) else "" for col in range(3)]
                    result.append(Subtitle(float(values[0]), float(values[1]), values[2]))
                self.captions = validate(sorted(result, key=lambda c: c.start))
                self.captions_dirty = True
                dialog.accept()
            except ValueError as exc:
                QMessageBox.warning(dialog, "Ungültige Untertitel", str(exc))
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def save_subtitles(self):
        path, selected = QFileDialog.getSaveFileName(self, "Untertitel / Transkript speichern", str(self.path.with_suffix(".srt")), "SRT (*.srt);;Transkript (*.txt)")
        if path:
            try:
                if Path(path).suffix.lower() == ".txt":
                    Path(path).write_text("\n".join(c.text for c in self.captions), encoding="utf-8")
                else:
                    from .subtitles import write_srt
                    write_srt(self.captions, path)
                self.status.setText("Untertitel / Transkript gespeichert")
                self.captions_dirty = False
            except Exception as exc:
                self.operation_failed(str(exc))

    def caption_position(self, milliseconds):
        seconds = milliseconds / 1000
        visible = self.show_captions.isChecked()
        self.caption_label.setText("\n".join(c.text for c in self.captions if c.start <= seconds < c.end) if visible else "")
        if self.player.subtitleTracks():
            self.player.setActiveSubtitleTrack(0 if visible else -1)

    def embed_subtitles(self):
        path, _ = QFileDialog.getSaveFileName(self, "MP4 mit Untertiteln", str(self.path.with_name(self.path.stem + "-untertitel.mp4")), "MP4 (*.mp4)")
        if path:
            from .subtitles import write_srt, embed_subtitles
            sidecar = Path(path).with_suffix(".srt")
            try:
                write_srt(self.captions, sidecar)
                self.submit(lambda: embed_subtitles(self.path, sidecar, path))
            except Exception as exc:
                self.operation_failed(str(exc))

    def closeEvent(self, event):
        if self.job:
            QMessageBox.information(self, "Verarbeitung läuft", "Bitte den laufenden Videoexport zuerst abschließen.")
            event.ignore()
            return
        if self.captions_dirty:
            answer = QMessageBox.question(self, "Untertitel speichern?", "Die Untertitel wurden geändert.",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Save:
                from .subtitles import write_srt
                try:
                    write_srt(self.captions, self.path.with_suffix(".srt"))
                except Exception as exc:
                    QMessageBox.warning(self, "Untertitel konnten nicht gespeichert werden", str(exc))
                    event.ignore()
                    return
        self.player.stop()
        self.player.setSource(QUrl())
        event.accept()
