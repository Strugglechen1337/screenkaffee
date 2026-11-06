"""Fortschrittsfenster für lange Downloads (Update, Untertitel-Erweiterung).

Der Hintergrund-Thread schreibt nur in ein einfaches dict (`Progress.state`); ein Qt-Timer im
UI-Thread liest es regelmäßig und aktualisiert das Fenster. So braucht der Thread keine Qt-Objekte.
"""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from .i18n import language, tr


class Cancelled(Exception):
    """Der Nutzer hat den Download abgebrochen."""


def decimal(text):
    """Dezimalkomma auf Deutsch, Punkt auf Englisch."""
    return text if language() == "en" else text.replace(".", ",")


def megabytes(value):
    return decimal(f"{value / 1e6:.0f}")


def duration(seconds):
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} min {rest:02d} s" if minutes < 10 else f"{minutes} min"


def describe(done, total, elapsed):
    """'45 von 110 MB · 3,2 MB/s · noch ca. 20 s' – ohne Tempo/Restzeit, solange es keine sinnvolle Schätzung gibt."""
    if total <= 0:
        return tr("{done} MB geladen", done=megabytes(done))
    text = tr("{done} von {total} MB", done=megabytes(done), total=megabytes(total))
    if elapsed >= 1 and done > 0:
        speed = done / elapsed
        text += " · " + decimal(f"{speed / 1e6:.1f}") + " MB/s"
        if done < total:
            text += " · " + tr("noch ca. {time}", time=duration((total - done) / speed))
    return text


def overall_percent(phase, done, total, phases):
    """Gesamtfortschritt 0–100 über mehrere Schritte: phases = [(name, Anteil), …], Anteile ergeben 1."""
    start = 0.0
    for name, share in phases:
        if name == phase:
            part = min(1.0, done / total) if total > 0 else 0.0
            return int(round(100 * (start + share * part)))
        start += share
    return 100


class Progress:
    """Gemeinsamer Zustand zwischen Hintergrund-Thread und Fenster."""

    def __init__(self, phases, clock=time.monotonic):
        self.phases = phases                       # [(name, Anteil, Beschriftung), …]
        self.clock = clock
        self.state = {"phase": phases[0][0], "done": 0, "total": 0, "cancel": False, "started": clock()}

    def stage(self, phase):
        self.state.update(phase=phase, done=0, total=0, started=self.clock())

    def report(self, done, total):
        """Callback für updater.download/extract; bricht per Ausnahme ab, wenn der Nutzer abbricht."""
        if self.state["cancel"]:
            raise Cancelled()
        self.state.update(done=done, total=total)

    def percent(self):
        return overall_percent(self.state["phase"], self.state["done"], self.state["total"],
                               [(name, share) for name, share, _ in self.phases])

    def step_text(self):
        names = [name for name, _, _ in self.phases]
        index = names.index(self.state["phase"]) if self.state["phase"] in names else len(names) - 1
        label = self.phases[index][2]
        return tr("Schritt {number} von {total}: {label}", number=index + 1, total=len(names), label=tr(label)) \
            if len(names) > 1 else tr(label)

    def detail_text(self):
        state = self.state
        if state["phase"] == self.phases[0][0]:
            return describe(state["done"], state["total"], self.clock() - state["started"])
        if state["total"] > 0:
            return tr("{done} von {total} Dateien", done=state["done"], total=state["total"])
        return ""


class ProgressWindow(QDialog):
    """Nicht-modales Fenster mit Balken; „Im Hintergrund“ blendet es aus, „Abbrechen“ setzt das Abbruch-Flag."""

    def __init__(self, title, heading, progress, parent=None, interval=200):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)
        self.setMinimumWidth(460)
        self.progress = progress
        self.finished_text = None
        layout = QVBoxLayout(self)
        self.heading = QLabel(heading)
        self.heading.setStyleSheet("font-weight: 600;")
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)
        self.step = QLabel()
        layout.addWidget(self.step)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(True)
        self.bar.setFormat("%p %")
        self.bar.setMinimumHeight(22)
        layout.addWidget(self.bar)
        self.detail = QLabel()
        self.detail.setObjectName("muted")
        layout.addWidget(self.detail)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.hide_button = QPushButton(tr("Im Hintergrund"))
        self.hide_button.clicked.connect(self.hide)
        self.cancel_button = QPushButton(tr("Abbrechen"))
        self.cancel_button.clicked.connect(self.request_cancel)
        self.close_button = QPushButton(tr("Schließen"))
        self.close_button.clicked.connect(self.close)
        self.close_button.hide()
        for button in (self.hide_button, self.cancel_button, self.close_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(interval)
        self.refresh()

    def refresh(self):
        if self.finished_text is not None:
            return
        self.step.setText(self.progress.step_text())
        self.bar.setValue(self.progress.percent())
        self.detail.setText(self.progress.detail_text())

    def request_cancel(self):
        self.progress.state["cancel"] = True
        self.cancel_button.setEnabled(False)
        self.step.setText(tr("Wird abgebrochen …"))

    def status_line(self):
        """Kurzfassung für die Statusleiste, solange das Fenster ausgeblendet ist."""
        return f"{self.heading.text()} – {self.bar.value()} %"

    def finish(self, text, success=True):
        """Endzustand anzeigen; das Fenster bleibt offen, bis der Nutzer es schließt (oder die App neu startet)."""
        self.finished_text = text
        self.timer.stop()
        if success:
            self.bar.setValue(100)
        self.step.setText(text)
        self.detail.setText("")
        self.hide_button.hide()
        self.cancel_button.hide()
        self.close_button.show()
        self.close_button.setDefault(True)

    def closeEvent(self, event):
        # Schließen während des Downloads = nur ausblenden; der Download läuft weiter.
        if self.finished_text is None:
            event.ignore()
            self.hide()
            return
        self.timer.stop()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.finished_text is None:
            self.hide()
            return
        super().keyPressEvent(event)
