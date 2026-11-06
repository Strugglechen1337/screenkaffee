"""Befehlssuche (Strg+K): jede Menüfunktion per Tippen finden und mit Enter ausführen."""
import unicodedata

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QLabel


def fold(text):
    """Klein, ohne Akzente, Umlaute als ae/oe/ue/ss, ohne „…“ – damit „zeitraf“, „groesse“ und „Größe“ passen."""
    text = text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss").replace("…", "")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def collect(menu, path=()):
    """[(Pfad-Text, QAction)] für alle auslösbaren Einträge eines Menübaums, Untermenüs rekursiv."""
    result = []
    for action in menu.actions():
        if action.isSeparator():
            continue
        text = action.text().replace("&", "")
        if action.menu() is not None:
            result += collect(action.menu(), path + (text,))
        elif text:
            result.append((" › ".join(path + (text,)), action))
    return result


def score(query, label):
    """None = passt nicht; sonst kleiner = besser (Wortanfang vor Wortmitte, kürzere Einträge zuerst)."""
    words = fold(query).split()
    target = fold(label)
    if not words:
        return len(target)
    total = 0
    for word in words:
        index = target.find(word)
        if index < 0:
            return None
        start_of_word = index == 0 or not target[index - 1].isalnum()
        total += index if not start_of_word else index // 4
    return total * 10 + len(target)


def search(query, entries, limit=40):
    ranked = [(score(query, label), position, label, action) for position, (label, action) in enumerate(entries)]
    ranked = [entry for entry in ranked if entry[0] is not None]
    ranked.sort(key=lambda entry: (entry[0], entry[1]))
    return [(label, action) for _, _, label, action in ranked[:limit]]


class CommandPalette(QDialog):
    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Befehl suchen")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setObjectName("commandPalette")
        self.setStyleSheet("#commandPalette{border:1px solid #4a5560}")
        self.resize(560, 420)
        self.entries = [(label, action) for label, action in entries if action.isEnabled()]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.field = QLineEdit()
        self.field.setPlaceholderText("Befehl suchen … (z. B. „zeitraffer“, „vergleichen“, „updates“)")
        self.field.setClearButtonEnabled(True)
        layout.addWidget(self.field)
        self.listing = QListWidget()
        layout.addWidget(self.listing)
        hint = QLabel("↑ ↓ auswählen · Enter ausführen · Esc schließen")
        hint.setObjectName("muted")
        layout.addWidget(hint)
        self.field.textChanged.connect(self.refresh)
        self.field.returnPressed.connect(self.run_current)
        self.listing.itemActivated.connect(lambda _: self.run_current())
        self.field.installEventFilter(self)
        self.chosen = None
        self.refresh("")

    def refresh(self, text=None):
        self.listing.clear()
        for label, action in search(self.field.text() if text is None else text, self.entries):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, label)
            self.listing.addItem(item)
        if self.listing.count():
            self.listing.setCurrentRow(0)

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if obj is self.field and event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            row = self.listing.currentRow() + (1 if event.key() == Qt.Key.Key_Down else -1)
            if 0 <= row < self.listing.count():
                self.listing.setCurrentRow(row)
            return True
        return super().eventFilter(obj, event)

    def run_current(self):
        item = self.listing.currentItem()
        if item is None:
            return
        label = item.data(Qt.ItemDataRole.UserRole)
        self.chosen = next(action for text, action in self.entries if text == label)
        self.accept()
