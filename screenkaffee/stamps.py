"""Local original stamps and a user-owned PNG library; no network access."""
from pathlib import Path
from .i18n import N_, tr
import uuid
from PIL import Image, ImageDraw, ImageFont
from .color import import_srgb, save_png
from .workspace import portable_root


def builtin_stamps():
    result = {}
    for name, color, glyph in ((N_("Erledigt"), "#429b58", "OK"), (N_("Fehler"), "#d84a40", "X"),
                               (N_("Hinweis"), "#378bc2", "i"), (N_("Achtung"), "#db982e", "!")):
        image = Image.new("RGBA", (128, 128))
        draw = ImageDraw.Draw(image)
        draw.ellipse((6, 6, 122, 122), fill=color, outline="white", width=5)
        font = ImageFont.load_default(size=50)
        draw.text((64, 64), glyph, fill="white", font=font, anchor="mm")
        result["builtin:" + name] = (tr(name), image)
    return result


class StampLibrary:
    def __init__(self, folder=None):
        self.folder = Path(folder) if folder else portable_root() / "stamps"

    def entries(self):
        result = builtin_stamps()
        if self.folder.is_dir():
            for path in self.folder.glob("*.png"):
                try:
                    result[path.name] = (path.stem.rsplit("--", 1)[0], import_srgb(path))
                except (OSError, ValueError):
                    continue
        return result

    def add(self, image, name):
        name = "".join(c for c in name.strip() if c.isalnum() or c in " -_")[:60] or "Stempel"  # i18n: ignore – stabiler Standarddateiname
        self.folder.mkdir(parents=True, exist_ok=True)
        key = name + "--" + uuid.uuid4().hex + ".png"
        save_png(image, self.folder / key)
        return key

    def remove(self, key):
        if key.startswith("builtin:") or Path(key).name != key or not key.endswith(".png"):
            raise ValueError(tr("Nur eigene Stempel können entfernt werden."))
        (self.folder / key).unlink(missing_ok=True)


def choose_stamp(parent):
    from PySide6.QtCore import Qt, QSize
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QPushButton, QDialogButtonBox, QFileDialog
    from .editor import pil_to_qimage
    library = StampLibrary()
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("Stempelbibliothek"))
    dialog.resize(560, 420)
    layout = QVBoxLayout(dialog)
    listing = QListWidget()
    listing.setViewMode(QListWidget.ViewMode.IconMode)
    listing.setIconSize(QSize(80, 80))
    listing.setGridSize(QSize(120, 115))
    layout.addWidget(listing)
    def refresh(selected=None):
        listing.clear()
        for key, (name, image) in library.entries().items():
            item = QListWidgetItem(QIcon(QPixmap.fromImage(pil_to_qimage(image))), name)
            item.setData(Qt.ItemDataRole.UserRole, key)
            listing.addItem(item)
            if key == selected:
                listing.setCurrentItem(item)
    def add():
        filename, _ = QFileDialog.getOpenFileName(dialog, tr("Eigenen Stempel hinzufügen"), "", tr('Bilder (*.png *.jpg *.jpeg *.webp *.bmp)'))
        if filename:
            refresh(library.add(import_srgb(filename), Path(filename).stem))
    def remove():
        item = listing.currentItem()
        if item and not item.data(Qt.ItemDataRole.UserRole).startswith("builtin:"):
            library.remove(item.data(Qt.ItemDataRole.UserRole))
            refresh()
    row = QHBoxLayout()
    for label, action in ((tr("Eigenen hinzufügen"), add), (tr("Eigenen entfernen"), remove)):
        button = QPushButton(label)
        button.clicked.connect(action)
        row.addWidget(button)
    layout.addLayout(row)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    listing.itemDoubleClicked.connect(lambda _: dialog.accept())
    layout.addWidget(buttons)
    refresh()
    if dialog.exec() == QDialog.DialogCode.Accepted and listing.currentItem():
        return library.entries()[listing.currentItem().data(Qt.ItemDataRole.UserRole)][1]
    return None
