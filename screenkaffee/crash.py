"""Fehlerbericht bei unerwarteten Fehlern: Version, System und Fehler zum Kopieren – ohne Bilder und Dateiinhalte."""
import os
from pathlib import Path
import platform
import sys
import traceback


def anonymize(text):
    """Benutzerordner und Benutzername aus Pfaden entfernen."""
    home = str(Path.home())
    for variant in {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}:
        if variant and len(variant) > 3:
            text = text.replace(variant, "~")
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if len(user) > 2:
        text = text.replace(os.sep + user + os.sep, os.sep + "<Benutzer>" + os.sep).replace("/" + user + "/", "/<Benutzer>/")
    return text


def crash_report(kind, value, tb):
    from . import __version__
    try:
        from .updater import installed_variant
        variant = installed_variant() or "Quellcode"
    except Exception:
        variant = "unbekannt"
    lines = [f"screenkaffee {__version__} ({variant})", f"System: {platform.platform()} · {platform.machine()}",
             f"Python: {sys.version.split()[0]}"]
    try:
        import PySide6
        from PySide6.QtGui import QGuiApplication
        lines.append(f"Qt/PySide6: {PySide6.__version__}")
        app = QGuiApplication.instance()
        if app:
            screens = ", ".join(f"{s.size().width()}×{s.size().height()} @{s.devicePixelRatio():g}" for s in app.screens())
            lines.append(f"Bildschirme: {screens or 'keine'} · Plattform: {app.platformName()}")
    except Exception:
        pass
    if os.environ.get("XDG_SESSION_TYPE"):
        lines.append(f"Sitzung: {os.environ['XDG_SESSION_TYPE']}" + (" (Hyprland)" if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") else ""))
    lines += ["", "Fehler:", "".join(traceback.format_exception(kind, value, tb)).rstrip()]
    return anonymize("\n".join(lines))


def show_crash_dialog(report, log_path=None, parent=None):
    """Zeigt den Bericht mit „Bericht kopieren“; liefert den Dialog (nicht modal, damit die App weiterläuft)."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QPushButton,
                                   QVBoxLayout)
    dialog = QDialog(parent)
    dialog.setWindowTitle("screenkaffee – unerwarteter Fehler")
    dialog.resize(640, 420)
    layout = QVBoxLayout(dialog)
    intro = QLabel("Da ist etwas schiefgegangen. Bereits gespeicherte Aufnahmen bleiben erhalten.\n"
                   "Mit „Bericht kopieren“ kannst du die Details weitergeben. Der Bericht enthält Version, System und "
                   "Fehlermeldung, aber keine Bilder und keine Dateiinhalte.")
    intro.setWordWrap(True)
    layout.addWidget(intro)
    text = QPlainTextEdit(report)
    text.setReadOnly(True)
    layout.addWidget(text)
    buttons = QDialogButtonBox()
    buttons.addButton("Schließen", QDialogButtonBox.ButtonRole.RejectRole)
    copy = QPushButton("Bericht kopieren")
    buttons.addButton(copy, QDialogButtonBox.ButtonRole.ActionRole)
    copy.clicked.connect(lambda: (QApplication.clipboard().setText(report), copy.setText("Kopiert ✓")))
    if log_path:
        log = QPushButton("Protokoll öffnen")
        buttons.addButton(log, QDialogButtonBox.ButtonRole.ActionRole)
        log.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_path))))
    buttons.rejected.connect(dialog.close)
    layout.addWidget(buttons)
    dialog.copy_button = copy
    dialog.show()
    dialog.raise_()
    return dialog
