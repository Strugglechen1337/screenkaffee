"""Markierungs-Vorlagen: häufig genutzte Kombinationen (z. B. Pfeil + „Hier klicken“) speichern und mit einem Klick einfügen.

Anders als Bildstempel bleiben Vorlagen bearbeitbare Markierungen. Eigene Vorlagen liegen lokal in
``data/annotation-templates.json``.
"""
import copy
from .i18n import tr
import json
from pathlib import Path

from .workspace import portable_root

FILE = "annotation-templates.json"
RED = "#e5332a"


def _text(x, y, text, color=RED, size=22, width=260):
    return {"kind": "text", "color": color, "width": 4, "points": [x, y, x + width, y + 60], "text": text,
            "text_width": width, "text_height": 60, "font_size": size, "pos": [0, 0]}


def builtin_templates():
    return [
        {"name": tr("Hier klicken"), "builtin": True, "items": [
            {"kind": "arrow", "color": RED, "width": 5, "points": [0, 0, 110, 80], "pos": [0, 0]},
            _text(-20, -60, tr("Hier klicken"))]},
        {"name": tr("Wichtig (Rahmen)"), "builtin": True, "items": [
            {"kind": "rectangle", "color": RED, "width": 4, "points": [0, 40, 300, 200], "pos": [0, 0]},
            _text(0, -10, tr("Wichtig"))]},
        {"name": tr("Neu"), "builtin": True, "items": [
            {"kind": "speech", "color": "#2c82c9", "width": 3, "points": [0, 0, 150, 70], "text": tr("Neu!"), "fill": "#ffffff",
             "text_width": 150, "text_height": 70, "font_size": 24, "pos": [0, 0]}]},
        {"name": tr("Schritt mit Erklärung"), "builtin": True, "items": [
            {"kind": "step", "color": RED, "width": 4, "points": [0, 0, 46, 46], "text": "1", "number": 1,
             "text_width": 46, "text_height": 46, "font_size": 22, "pos": [0, 0]},
            _text(56, 4, tr("Erklärung"), color="#172033")]},
    ]


def bounds(items):
    """(links, oben, rechts, unten) aller Markierungen in Bildkoordinaten."""
    xs, ys = [], []
    for item in items:
        px, py = item.get("pos", [0, 0])
        x1, y1, x2, y2 = item["points"]
        if item["kind"] in ("text", "speech", "step"):
            x2, y2 = x1 + item.get("text_width", 300), y1 + item.get("text_height", 80)
        xs += [x1 + px, x2 + px]
        ys += [y1 + py, y2 + py]
    return min(xs), min(ys), max(xs), max(ys)


def shifted(items, dx, dy):
    result = copy.deepcopy(items)
    for item in result:
        px, py = item.get("pos", [0, 0])
        item["pos"] = [px + dx, py + dy]
    return result


def normalize(items):
    """Linke obere Ecke auf (0, 0), damit die Vorlage überall passt."""
    left, top, _, _ = bounds(items)
    return shifted(items, -left, -top)


def placed(items, center):
    """Markierungen so verschoben, dass ihre Mitte auf ``center`` (x, y) liegt."""
    left, top, right, bottom = bounds(items)
    return shifted(items, center[0] - (left + right) / 2, center[1] - (top + bottom) / 2)


class TemplateLibrary:
    def __init__(self, folder=None):
        self.path = Path(folder or portable_root()) / FILE

    def own(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [entry for entry in data if isinstance(entry, dict) and entry.get("name") and entry.get("items")]
        except (OSError, ValueError):
            return []

    def entries(self):
        return builtin_templates() + self.own()

    def _write(self, entries):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(self.path)

    def add(self, name, items):
        name = " ".join(name.split())[:60]
        if not name:
            raise ValueError(tr("Bitte einen Namen für die Vorlage angeben."))
        if not items:
            raise ValueError(tr("Bitte zuerst Markierungen auswählen."))
        if any(entry["name"] == name for entry in builtin_templates()):
            raise ValueError(tr("Diesen Namen hat schon eine mitgelieferte Vorlage."))
        entries = [entry for entry in self.own() if entry["name"] != name]
        cleaned = [{key: value for key, value in item.items() if key != "rotation" or value} for item in items]
        entries.append({"name": name, "items": normalize(cleaned)})
        self._write(entries)
        return name

    def remove(self, name):
        entries = self.own()
        if not any(entry["name"] == name for entry in entries):
            raise ValueError(tr("Nur eigene Vorlagen können gelöscht werden."))
        self._write([entry for entry in entries if entry["name"] != name])
