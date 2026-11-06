"""Aufräum-Helfer: doppelte und sehr ähnliche Aufnahmen, große alte Videos und der Papierkorb – mit Platzangabe.

Ähnlichkeit per Differenz-Hash (dHash, 64 Bit) aus den Vorschaubildern; nichts verlässt den Rechner.
Gelöscht wird nur, was der Nutzer ankreuzt, und zuerst in den Papierkorb (wiederherstellbar).
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

SIMILAR = 5                      # höchstens so viele abweichende Bits gelten als „sehr ähnlich“
BIG_VIDEO = 50 * 1000 * 1000
OLD_DAYS = 90
OLD_VIDEO = 10 * 1000 * 1000


def dhash(image, size=8):
    """64-Bit-Differenz-Hash: Helligkeitsgefälle zwischen Nachbarpixeln eines 9×8-Graubilds."""
    import numpy as np
    small = np.asarray(image.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS), dtype=np.int16)
    value = 0
    for bit in (small[:, :-1] > small[:, 1:]).flatten():
        value = value << 1 | int(bit)
    return value


def distance(a, b):
    return bin(a ^ b).count("1")


def human(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1000 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}".replace(".", ",")
        size /= 1000


@dataclass
class Entry:
    ident: str
    title: str
    created: str
    kind: str
    bytes: int
    width: int = 0
    height: int = 0
    hash: int = None


@dataclass
class Report:
    groups: list = field(default_factory=list)      # [[Entry, …]] – erstes Element = Vorschlag zum Behalten
    videos: list = field(default_factory=list)      # [(Entry, Grund)]
    trash_bytes: int = 0
    scanned: int = 0

    def suggested(self):
        """IDs, die standardmäßig angekreuzt sind: alles außer dem ersten Bild jeder Gruppe."""
        return [entry.ident for group in self.groups for entry in group[1:]]

    def reclaimable(self, ids=None):
        ids = set(self.suggested() if ids is None else ids)
        seen, total = set(), 0
        for entry in [e for group in self.groups for e in group] + [e for e, _ in self.videos]:
            if entry.ident in ids and entry.ident not in seen:
                seen.add(entry.ident)
                total += entry.bytes
        return total


def entry_bytes(workspace, row):
    path = workspace.images / row["filename"]
    files = {path, workspace.images / (row["id"] + ".thumb.png"), path.with_suffix(".screenkaffee.json"),
             path.with_suffix(".prism.json"), path.with_suffix(".srt"), path.with_suffix(".txt")}
    return sum(file.stat().st_size for file in files if file.is_file())


def folder_bytes(folder):
    folder = Path(folder)
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file()) if folder.is_dir() else 0


def group_similar(entries, threshold=SIMILAR):
    """Gruppen ähnlicher Bilder (Union-Find über alle Paare); je Gruppe das neueste zuerst."""
    entries = [e for e in entries if e.hash is not None]
    parent = list(range(len(entries)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            if distance(entries[i].hash, entries[j].hash) <= threshold:
                parent[root(j)] = root(i)
    groups = {}
    for i, entry in enumerate(entries):
        groups.setdefault(root(i), []).append(entry)
    result = [sorted(group, key=lambda e: e.created, reverse=True) for group in groups.values() if len(group) > 1]
    return sorted(result, key=lambda group: -sum(e.bytes for e in group[1:]))


def is_identical(first, second):
    return first.hash == second.hash and (first.width, first.height) == (second.width, second.height)


def scan(workspace, now=None, progress=None):
    now = now or datetime.now(timezone.utc)
    rows = workspace.rows()
    images, report = [], Report(scanned=len(rows))
    for index, row in enumerate(rows):
        entry = Entry(row["id"], row["title"], row["created"], row["kind"], entry_bytes(workspace, row), row["width"] or 0, row["height"] or 0)
        if row["kind"] == "image":
            try:
                with Image.open(workspace.thumbnail_path(row["id"])) as image:
                    entry.hash = dhash(image)
            except (OSError, ValueError):
                pass
            images.append(entry)
        elif row["kind"] == "video":
            created = datetime.fromisoformat(row["created"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            old = now - created > timedelta(days=OLD_DAYS)
            if entry.bytes >= BIG_VIDEO:
                report.videos.append((entry, "groß" + (", älter als 90 Tage" if old else "")))
            elif old and entry.bytes >= OLD_VIDEO:
                report.videos.append((entry, "älter als 90 Tage"))
        if progress:
            progress(index + 1, len(rows))
    report.groups = group_similar(images)
    report.videos.sort(key=lambda item: -item[0].bytes)
    report.trash_bytes = folder_bytes(workspace.root / "trash")
    return report


def cleanup_dialog(parent, workspace, report, on_change=None):
    """Ergebnis anzeigen; angekreuzte Aufnahmen in den Papierkorb, Papierkorb auf Wunsch leeren."""
    from PySide6.QtCore import Qt, QSize
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem,
                                   QPushButton, QMessageBox, QHeaderView)
    dialog = QDialog(parent)
    dialog.setWindowTitle("Aufräumen")
    dialog.resize(760, 560)
    layout = QVBoxLayout(dialog)
    summary = QLabel()
    summary.setWordWrap(True)
    layout.addWidget(summary)
    tree = QTreeWidget()
    tree.setHeaderLabels(["Aufnahme", "Größe", "Datum"])
    tree.setIconSize(QSize(64, 40))
    tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    layout.addWidget(tree)
    dialog.tree, dialog.summary = tree, summary
    suggested = set(report.suggested())

    def add_entry(section, entry, note=""):
        item = QTreeWidgetItem(section, [entry.title + (f"  ({note})" if note else ""), human(entry.bytes), entry.created[:16].replace("T", " ")])
        item.setData(0, Qt.ItemDataRole.UserRole, entry.ident)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Checked if entry.ident in suggested else Qt.CheckState.Unchecked)
        try:
            item.setIcon(0, QIcon(QPixmap(str(workspace.thumbnail_path(entry.ident)))))
        except Exception:
            pass
        return item
    if report.groups:
        section = QTreeWidgetItem(tree, [f"Doppelte und sehr ähnliche Bilder ({len(report.groups)} Gruppen)"])
        for number, group in enumerate(report.groups, 1):
            kind = "gleich" if all(is_identical(group[0], other) for other in group[1:]) else "sehr ähnlich"
            node = QTreeWidgetItem(section, [f"Gruppe {number}: {len(group)} Bilder, {kind}"])
            for position, entry in enumerate(group):
                add_entry(node, entry, "neuestes – behalten" if position == 0 else "")
    if report.videos:
        section = QTreeWidgetItem(tree, [f"Große und alte Videos ({len(report.videos)})"])
        for entry, reason in report.videos:
            add_entry(section, entry, reason)
    if not report.groups and not report.videos:
        QTreeWidgetItem(tree, ["Nichts gefunden – keine doppelten Bilder und keine großen alten Videos."])
    tree.expandAll()

    def checked():
        result = []
        stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            stack += [item.child(i) for i in range(item.childCount())]
            ident = item.data(0, Qt.ItemDataRole.UserRole)
            if ident and item.checkState(0) == Qt.CheckState.Checked:
                result.append(ident)
        return result
    dialog.checked = checked

    def update_summary(*_):
        ids = checked()
        summary.setText(f"{report.scanned} Aufnahmen geprüft. Angekreuzt: {len(ids)} ({human(report.reclaimable(ids))}). "
                        "Sie kommen in den Papierkorb und lassen sich über „Gelöschte Aufnahmen wiederherstellen“ zurückholen; "
                        f"Platz wird erst frei, wenn der Papierkorb geleert wird. Papierkorb jetzt: {human(report.trash_bytes)}.")
    tree.itemChanged.connect(update_summary)
    update_summary()

    def remove():
        ids = checked()
        if not ids:
            return
        if QMessageBox.question(dialog, "Aufräumen", f"{len(ids)} Aufnahmen in den Papierkorb verschieben?") != QMessageBox.StandardButton.Yes:
            return
        for ident in ids:
            workspace.remove(ident)
        report.trash_bytes = folder_bytes(workspace.root / "trash")
        stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            stack += [item.child(i) for i in range(item.childCount())]
            if item.data(0, Qt.ItemDataRole.UserRole) in ids:
                item.parent().removeChild(item)
        empty_button.setText(f"Papierkorb leeren ({human(report.trash_bytes)}) …")
        update_summary()
        if on_change:
            on_change()

    def empty():
        if not report.trash_bytes:
            return
        if QMessageBox.question(dialog, "Papierkorb leeren",
                                f"Den Papierkorb endgültig leeren? {human(report.trash_bytes)} werden frei. Das lässt sich nicht rückgängig machen.") != QMessageBox.StandardButton.Yes:
            return
        freed = workspace.empty_trash()
        report.trash_bytes = 0
        empty_button.setText("Papierkorb leeren (0 B) …")
        update_summary()
        QMessageBox.information(dialog, "Papierkorb leeren", f"{human(freed)} freigegeben.")
    row = QHBoxLayout()
    remove_button = QPushButton("Angekreuzte in den Papierkorb")
    remove_button.clicked.connect(remove)
    empty_button = QPushButton(f"Papierkorb leeren ({human(report.trash_bytes)}) …")
    empty_button.clicked.connect(empty)
    close_button = QPushButton("Schließen")
    close_button.clicked.connect(dialog.accept)
    for button in (remove_button, empty_button):
        row.addWidget(button)
    row.addStretch()
    row.addWidget(close_button)
    layout.addLayout(row)
    dialog.remove_checked, dialog.empty_trash = remove, empty
    return dialog
