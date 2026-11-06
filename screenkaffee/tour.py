"""Erster-Start-Tour: führt in wenigen Schritten durch die besten Funktionen.

Die Tour ist eine durchsichtige Ebene über dem Hauptfenster. Sie dunkelt alles ab, lässt die gerade
erklärte Schaltfläche hell und zeigt daneben eine Sprechblase mit „Zurück“, „Weiter“ und „Tour beenden“.
Die Schritte selbst sind reine Daten (`steps`), die Platzierung der Sprechblase ist reine Geometrie
(`bubble_position`) – beides ohne Fenster testbar.
"""
from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .hotkeys import display_hotkey
from .i18n import tr

MARGIN = 8          # Abstand des hellen Rahmens um die Schaltfläche
GAP = 14            # Abstand zwischen Rahmen und Sprechblase


def key(settings, action, fallback=""):
    """Die eingestellte Taste für eine Aktion in der Sprache der Oberfläche, z. B. 'Strg+Alt+D'."""
    value = (settings.get("hotkeys") or {}).get(action) or fallback
    return display_hotkey(value) if value else ""


def steps(settings):
    """Die Tour-Schritte: (Schlüssel, Ziel-Attribut am Hauptfenster oder None, Titel, Text)."""
    region = key(settings, "region")
    draw = key(settings, "draw")
    region_hint = tr("Noch schneller: Taste <b>{key}</b> – auch wenn screenkaffee nur im Tray läuft.", key=region) if region else \
        tr("In den Einstellungen kannst du dafür eine Taste festlegen.")
    draw_hint = tr("<b>{key}</b> schaltet es ein und aus.", key=draw) if draw else tr("Du findest es unter „Mehr → Aufnehmen“.")
    return [
        ("welcome", None, tr("Willkommen bei screenkaffee ☕"),
         tr("In einer Minute zeige ich dir die besten Funktionen. Mit den Pfeiltasten blätterst du, "
            "<b>Esc</b> beendet die Tour. Du findest sie jederzeit wieder unter „Mehr → Hilfe und Updates“.")),
        ("capture", "capture_button", tr("Aufnehmen"),
         tr("Bereich aufziehen, Fenster anklicken oder den ganzen Bildschirm nehmen. Beim Aufziehen rastet "
            "die Auswahl an Fenstern ein, die Lupe hilft pixelgenau. {hint}", hint=region_hint)),
        ("palette", None, tr("Alles mit Strg+K finden"),
         tr("Du weißt nicht, wo eine Funktion steckt? <b>Strg+K</b> öffnet die Befehlssuche – ein paar "
            "Buchstaben tippen, Eingabe drücken, fertig.")),
        ("compare", "list", tr("Screenshots vergleichen"),
         tr("Zwei Aufnahmen mit <b>Strg+Klick</b> markieren, Rechtsklick → <b>„Vergleichen (vorher ↔ nachher)“</b>. "
            "Ein Schieberegler blendet zwischen beiden über, jede Änderung wird rot markiert – ideal, um "
            "zu sehen, was sich an einer Seite oder einem Programm verändert hat.")),
        ("editor", "edit_button", tr("Bearbeiten wie ein Profi"),
         tr("Doppelklick auf eine Aufnahme öffnet den Editor: Pfeile, Text, Schrittnummern, Weichzeichnen, "
            "Lineal und fertige Vorlagen. Alles bleibt später änderbar.")),
        ("beautify", "share_button", tr("Schicker Screenshot & aufs Handy"),
         tr("Unter <b>Teilen</b> bekommt dein Bild mit einem Klick Hintergrund, Rahmen und Schatten. "
            "„Aufs Handy senden“ zeigt einen QR-Code – das Handy lädt das Bild direkt über dein WLAN, ohne Cloud.")),
        ("video", "video_button", tr("Videos mit Zoom und Tastendrücken"),
         tr("Bildschirmvideos mit Ton, Webcam, Klick-Kreisen und eingeblendeten Tastenkürzeln. Über den Pfeil "
            "daneben gibt es auch Zeitraffer und Android-Video.")),
        ("steps", "more_button", tr("Anleitungen von selbst"),
         tr("<b>Mehr → Aufnehmen → Schritt-Anleitung</b>: Klick dich einfach durch ein Programm. Bei jedem Klick "
            "entsteht ein Bild mit markierter Klickstelle – am Ende wird daraus ein fertiges Dokument.")),
        ("livedraw", None, tr("Live auf den Bildschirm zeichnen"),
         tr("Perfekt für Präsentationen und Bildschirmvideos: direkt über allen Fenstern zeichnen. {hint}"
            " Im selben Menü steckt auch das <b>Lineal</b> zum Ausmessen.", hint=draw_hint)),
        ("version", "version_button", tr("Immer aktuell"),
         tr("Hier unten steht deine Version. Ein Klick sucht nach Updates – mit Fortschrittsbalken, und ohne "
            "dass deine Aufnahmen angefasst werden. Viel Spaß mit screenkaffee!")),
    ]


def bubble_position(target, size, area):
    """Obere linke Ecke der Sprechblase (QPoint).

    target: QRect der hervorgehobenen Schaltfläche (oder None = mittig), size: QSize der Blase,
    area: QRect der Fläche. Bevorzugt unterhalb, sonst oberhalb, sonst rechts daneben, sonst mittig auf dem Ziel;
    bleibt immer im Bereich.
    """
    width, height = size.width(), size.height()

    def clamp(x, y):
        x = max(area.left() + GAP, min(x, area.right() - width - GAP))
        y = max(area.top() + GAP, min(y, area.bottom() - height - GAP))
        return QPoint(x, y)

    if target is None or target.isEmpty():
        return clamp(area.center().x() - width // 2, area.center().y() - height // 2)
    ring = target.adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN)
    centered_x = ring.center().x() - width // 2
    if ring.bottom() + GAP + height <= area.bottom() - GAP:
        return clamp(centered_x, ring.bottom() + GAP)
    if ring.top() - GAP - height >= area.top() + GAP:
        return clamp(centered_x, ring.top() - GAP - height)
    if ring.right() + GAP + width <= area.right() - GAP:
        return clamp(ring.right() + GAP, ring.center().y() - height // 2)
    return clamp(centered_x, ring.center().y() - height // 2)      # großes Ziel: mittig darauf


class TourOverlay(QWidget):
    """Abgedunkelte Ebene über dem Hauptfenster mit Spotlight und Sprechblase."""

    finished = Signal(bool)         # True = bis zum Ende angesehen, False = vorher beendet

    def __init__(self, window, tour_steps):
        super().__init__(window)
        self.window = window
        self.tour_steps = tour_steps
        self.index = 0
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.bubble = QFrame(self)
        self.bubble.setObjectName("tourBubble")
        self.bubble.setStyleSheet(
            "#tourBubble { background: #fffaf3; border: 2px solid #e8912d; border-radius: 12px; }"
            "#tourBubble QLabel { color: #2b2118; background: transparent; }"
            "#tourTitle { font-size: 16px; font-weight: 700; }"
            "#tourCounter { color: #9a7b5c; font-size: 11px; }"
            "#tourBubble QPushButton { padding: 5px 14px; border-radius: 6px; border: 1px solid #d9c6b0;"
            " background: #ffffff; color: #2b2118; }"
            "#tourBubble QPushButton#tourNext { background: #e8912d; border-color: #e8912d; color: white; font-weight: 600; }"
            "#tourBubble QPushButton#tourSkip { border: none; background: transparent; color: #9a7b5c; }")
        self.bubble.setFixedWidth(380)
        layout = QVBoxLayout(self.bubble)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)
        self.counter = QLabel()
        self.counter.setObjectName("tourCounter")
        layout.addWidget(self.counter)
        self.title = QLabel()
        self.title.setObjectName("tourTitle")
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.text)
        buttons = QHBoxLayout()
        self.skip_button = QPushButton(tr("Tour beenden"))
        self.skip_button.setObjectName("tourSkip")
        self.skip_button.clicked.connect(lambda: self.end(False))
        buttons.addWidget(self.skip_button)
        buttons.addStretch(1)
        self.back_button = QPushButton(tr("Zurück"))
        self.back_button.clicked.connect(self.back)
        buttons.addWidget(self.back_button)
        self.next_button = QPushButton(tr("Weiter"))
        self.next_button.setObjectName("tourNext")
        self.next_button.clicked.connect(self.forward)
        buttons.addWidget(self.next_button)
        layout.addLayout(buttons)
        for button in (self.skip_button, self.back_button, self.next_button):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        window.installEventFilter(self)
        self.setGeometry(window.rect())
        self.show_step(0)
        self.show()
        self.raise_()
        self.setFocus()

    # --- Ablauf ----------------------------------------------------------------------------------
    def current_key(self):
        return self.tour_steps[self.index][0]

    def target_rect(self):
        """Rechteck der aktuellen Schaltfläche in Koordinaten der Ebene, oder None."""
        attribute = self.tour_steps[self.index][1]
        widget = getattr(self.window, attribute, None) if attribute else None
        if widget is None or not widget.isVisible():
            return None
        return QRect(widget.mapTo(self.window, QPoint(0, 0)), widget.size())

    def show_step(self, index):
        self.index = max(0, min(index, len(self.tour_steps) - 1))
        _, _, title, text = self.tour_steps[self.index]
        last = self.index == len(self.tour_steps) - 1
        self.counter.setText(tr("Schritt {number} von {total}", number=self.index + 1, total=len(self.tour_steps)))
        self.title.setText(title)
        self.text.setText(text)
        self.back_button.setVisible(self.index > 0)
        self.next_button.setText(tr("Los geht’s!") if last else tr("Weiter"))
        self.skip_button.setVisible(not last)
        self.place_bubble()
        self.update()

    def place_bubble(self):
        self.bubble.adjustSize()
        self.bubble.move(bubble_position(self.target_rect(), self.bubble.size(), self.rect()))

    def forward(self):
        if self.index >= len(self.tour_steps) - 1:
            self.end(True)
        else:
            self.show_step(self.index + 1)

    def back(self):
        self.show_step(self.index - 1)

    def end(self, completed):
        self.window.removeEventFilter(self)
        self.finished.emit(completed)
        self.close()

    # --- Qt ----------------------------------------------------------------------------------------
    def eventFilter(self, watched, event):
        if watched is self.window and event.type() == QEvent.Type.Resize:
            self.setGeometry(self.window.rect())
            self.place_bubble()
        return False

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.end(False)
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.forward()
        elif event.key() == Qt.Key.Key_Left:
            self.back()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        event.accept()          # Klicks neben die Sprechblase lösen nichts im Hauptfenster aus

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shade = QPainterPath()
        shade.addRect(QRectF(self.rect()))
        target = self.target_rect()
        if target is not None:
            ring = QRectF(target.adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN))
            hole = QPainterPath()
            hole.addRoundedRect(ring, 10, 10)
            shade = shade.subtracted(hole)
        painter.fillPath(shade, QColor(20, 14, 8, 165))
        if target is not None:
            painter.setPen(QPen(QColor("#e8912d"), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(ring, 10, 10)
