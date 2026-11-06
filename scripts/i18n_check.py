"""Prüfhilfe für die Übersetzung: findet deutsche Texte, die (noch) nicht übersetzt werden.

Aufruf:  python scripts/i18n_check.py [datei.py ...]
Ohne Argumente werden alle Module unter screenkaffee/ geprüft.

Gemeldet werden
- MISSING: tr("…")/N_("…") mit Text ohne Eintrag im englischen Wörterbuch,
- UNWRAPPED: Zeichenketten/f-Strings, die deutsch aussehen und nicht in tr()/N_() stehen.
UNWRAPPED ist eine Heuristik: Log-Meldungen, Dateinamen, Einstellungsschlüssel usw. sind
erlaubt. Eine Zeile mit dem Kommentar  # i18n: ignore  wird übersprungen.
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
GERMAN = re.compile(r"[äöüÄÖÜß]|\b(und|oder|der|die|das|den|dem|nicht|kein|keine|Aufnahme|Aufnahmen|Datei|Bild|Fehler|wird|werden|mit|für|von|bitte|Bitte|Einstellungen|Speichern|Öffnen|Abbrechen|Schließen|Neu|Video|auswählen|Ordner|Sammlung|Markierung)\b")
SKIP_FILES = {"parity.py", "i18n.py"}
LOG_CALLS = {"debug", "info", "warning", "error", "exception", "critical"}


def literal(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def check(path, catalog):
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    wrapped, missing, unwrapped = set(), [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name in ("tr", "N_") and node.args:
                text = literal(node.args[0])
                if text is not None:
                    wrapped.add(id(node.args[0]))
                    if text not in catalog:
                        missing.append((node.lineno, text))
                # tr(variable) ist erlaubt: der Text muss dann an seiner Quelle mit N_() markiert sein.
            if name in LOG_CALLS:
                for arg in node.args:
                    for sub in ast.walk(arg):
                        wrapped.add(id(sub))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and literal(first.value) is not None:
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if id(node) in wrapped or id(node) in docstrings:
            continue
        text = None
        if literal(node) is not None:
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(v.value for v in node.values if isinstance(v, ast.Constant))
            for v in node.values:
                wrapped.add(id(v))
        if text and len(text) > 2 and GERMAN.search(text):
            line = lines[node.lineno - 1] if node.lineno - 1 < len(lines) else ""
            if "i18n: ignore" not in line:
                unwrapped.append((node.lineno, text))
    return missing, sorted(set(unwrapped))


def main(argv):
    from screenkaffee.i18n_en import CATALOG
    files = [Path(a) for a in argv] or sorted((ROOT / "screenkaffee").glob("*.py"))
    total_missing = total_unwrapped = 0
    for path in files:
        if path.name in SKIP_FILES:
            continue
        missing, unwrapped = check(path, CATALOG)
        total_missing += len(missing)
        total_unwrapped += len(unwrapped)
        for line, text in missing:
            print(f"{path.name}:{line}: MISSING {text!r}")
        for line, text in unwrapped:
            print(f"{path.name}:{line}: UNWRAPPED {text[:90]!r}")
    print(f"\n{total_missing} ohne englischen Eintrag, {total_unwrapped} vermutlich nicht übersetzte Texte.")
    return 1 if total_missing else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
