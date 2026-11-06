from pathlib import Path
from PySide6.QtCore import Qt, QUrl, QThread, Signal, Slot
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QDoubleSpinBox, QSpinBox, QCheckBox, QFileDialog, QSlider, QFormLayout, QMessageBox,
    QDialog, QDialogButtonBox, QTableWidget, QTableWidgetItem, QInputDialog, QComboBox)
from .media import probe_media, edit_video, merge_videos, extract_frame, export_html_page, VIDEO_SIZES


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
        self.crop = []
        for name, default, maximum in [("X", 0, self.info.width), ("Y", 0, self.info.height),
            ("Breite", self.info.width, self.info.width), ("Höhe", self.info.height, self.info.height)]:
            widget = QSpinBox()
            widget.setRange(0, maximum)
            widget.setValue(default)
            self.crop.append(widget)
            form3.addRow(name, widget)
        options.addLayout(form3)
        layout.addLayout(options)
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
        if path.lower().endswith(".html"):
            quality = self.quality.currentData()
            self.submit(lambda: export_html_page(self.path, path, quality))
            return
        options = {"start": self.start.value(), "end": self.end.value(), "speed": self.speed.value(),
            "mute": self.mute.isChecked(), "volume": self.volume.value(), "width": self.width.value(),
            "crop": tuple(w.value() for w in self.crop) if self.crop_enabled.isChecked() else None,
            "size": self.size_box.currentData(), "resize_mode": self.resize_mode.currentData(),
            "quality": self.quality.currentData(), "fps": self.fps.value(), "loops": self.animation_loops}
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
