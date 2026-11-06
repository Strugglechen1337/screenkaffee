"""Aufnahmen per QR-Code aufs Handy im selben WLAN.

Ein kleiner Webserver läuft nur, solange das Fenster offen ist (höchstens 15 Minuten), lauscht nur auf der gewählten
lokalen Adresse und liefert nur die freigegebenen Dateien unter einem zufälligen Pfad aus. Nichts geht ins Internet.
"""
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import mimetypes
from pathlib import Path
import secrets
import shutil
import socket
import threading
from urllib.parse import quote, urlsplit, parse_qs

from PIL import Image

TIMEOUT = 15 * 60
CHUNK = 256 * 1024
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
VIDEO_TYPES = {".mp4", ".webm", ".mov"}


def is_lan(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.version == 4 and ip.is_private and not ip.is_loopback and not ip.is_link_local


def local_addresses():
    """Private IPv4-Adressen dieses Rechners, die Adresse der Standardroute zuerst."""
    found = []
    try:
        # Ein UDP-"connect" verschickt nichts, er fragt nur das Betriebssystem nach der passenden Netzwerkkarte.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    unique = []
    for address in found:
        if is_lan(address) and address not in unique:
            unique.append(address)
    return unique


def qr_image(text, size=300):
    """QR-Code als PIL-Bild (schwarz auf weiß, mit Ruhezone), auf etwa `size` Pixel vergrößert."""
    import numpy as np
    import zxingcpp
    code = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode)
    modules = Image.fromarray(np.array(code.to_image(scale=1, add_quiet_zones=True)), "L")
    factor = max(1, size // modules.width)
    return modules.resize((modules.width * factor, modules.height * factor), Image.Resampling.NEAREST)


def content_type(path):
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def page(files, token):
    items = []
    for index, (path, title) in enumerate(files):
        link = f"/{token}/file/{index}"
        name = html.escape(title or path.name)
        suffix = path.suffix.lower()
        if suffix in IMAGE_TYPES:
            preview = f'<img src="{link}" alt="{name}">'
        elif suffix in VIDEO_TYPES:
            preview = f'<video src="{link}" controls playsinline preload="metadata"></video>'
        else:
            preview = ""
        items.append(f'<section>{preview}<p>{name}</p><a class="button" href="{link}?download=1" download>Herunterladen</a></section>')
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>screenkaffee</title>
<style>body{{font-family:system-ui,sans-serif;margin:0;padding:16px;background:#f4f4f4;color:#222}}
h1{{font-size:20px}}section{{background:#fff;border-radius:10px;padding:12px;margin:0 0 16px;box-shadow:0 1px 4px #0002}}
img,video{{max-width:100%;border-radius:6px;display:block}}p{{margin:10px 0;word-break:break-word}}
.button{{display:inline-block;background:#e8912d;color:#fff;padding:10px 18px;border-radius:20px;text-decoration:none;font-weight:600}}
small{{color:#777}}</style></head><body><h1>screenkaffee</h1>{''.join(items)}
<small>Bilder lassen sich auch lange antippen und speichern. Die Freigabe endet, sobald das Fenster am PC geschlossen wird.</small>
</body></html>""".encode("utf-8")


class Share:
    """Freigabe einer oder mehrerer Dateien unter http://<lokale Adresse>:<Port>/<Zufallspfad>/."""

    def __init__(self, files, address=None, port=0, timeout=TIMEOUT):
        self.files = [(Path(path), title) for path, title in files]
        if not self.files or not all(path.is_file() for path, _ in self.files):
            raise ValueError("Keine Datei zum Senden vorhanden.")
        if address is None:
            addresses = local_addresses()
            if not addresses:
                raise RuntimeError("Kein lokales Netzwerk gefunden. PC und Handy müssen im selben WLAN sein.")
            address = addresses[0]
        self.address = address
        self.token = secrets.token_urlsafe(12)
        self.visits = 0
        self.downloads = 0
        self.expired = False
        self.stopped = False
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer((address, port), self.handler())
        self.server.daemon_threads = True
        self.url = f"http://{address}:{self.server.server_address[1]}/{self.token}/"
        self.thread = threading.Thread(target=self.server.serve_forever, name="screenkaffee-lan-share", daemon=True)
        self.thread.start()
        self.timer = threading.Timer(timeout, self.expire)
        self.timer.daemon = True
        self.timer.start()

    def expire(self):
        self.expired = True
        self.stop()

    def stop(self):
        with self.lock:
            if self.stopped:
                return
            self.stopped = True
        self.timer.cancel()
        self.server.shutdown()
        self.server.server_close()

    def count(self, field):
        with self.lock:
            setattr(self, field, getattr(self, field) + 1)

    def handler(self):
        share = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "screenkaffee"
            sys_version = ""

            def log_message(self, *args):
                pass

            def do_GET(self):
                parts = urlsplit(self.path)
                prefix = f"/{share.token}/"
                if not secrets.compare_digest(parts.path[:len(prefix)].encode(), prefix.encode()):
                    return self.send_error(404)
                rest = parts.path[len(prefix):]
                if rest == "":
                    share.count("visits")
                    return self.reply(200, "text/html; charset=utf-8", page(share.files, share.token))
                if rest.startswith("file/") and rest[5:].isdigit() and int(rest[5:]) < len(share.files):
                    path, title = share.files[int(rest[5:])]
                    download = "download" in parse_qs(parts.query)
                    return self.send_file(path, title, download)
                self.send_error(404)

            def reply(self, status, kind, body):
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def send_file(self, path, title, download):
                name = (title or path.stem).strip() or path.stem
                name = "".join(ch for ch in name if ch not in '\\/:*?"<>|\r\n') + path.suffix.lower()
                self.send_response(200)
                self.send_header("Content-Type", content_type(path))
                self.send_header("Content-Length", str(path.stat().st_size))
                self.send_header("Cache-Control", "no-store")
                disposition = "attachment" if download else "inline"
                self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(name)}")
                self.end_headers()
                try:
                    with path.open("rb") as source:
                        shutil.copyfileobj(source, self.wfile, CHUNK)
                except (BrokenPipeError, ConnectionResetError):
                    return
                if download:
                    share.count("downloads")

        return Handler


def status_text(share):
    if share.expired:
        return "Freigabe abgelaufen – zum erneuten Senden das Fenster neu öffnen."
    if share.downloads:
        return f"✓ {share.downloads}× heruntergeladen"
    if share.visits:
        return "Seite auf dem Handy geöffnet …"
    return "Warte auf das Handy …"


def share_dialog(parent, files, cleanup=None):
    """Fenster mit QR-Code, Link und Status. Schließen beendet die Freigabe und löscht vorbereitete Dateien."""
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox,
                                   QApplication)
    from .editor import pil_to_qimage
    addresses = local_addresses()
    if not addresses:
        raise RuntimeError("Kein lokales Netzwerk gefunden. PC und Handy müssen im selben WLAN sein.")
    dialog = QDialog(parent)
    dialog.setWindowTitle("Aufs Handy senden")
    dialog.setMinimumWidth(420)
    layout = QVBoxLayout(dialog)
    intro = QLabel("Mit der Handykamera scannen. Handy und PC müssen im selben WLAN sein.")
    intro.setWordWrap(True)
    layout.addWidget(intro)
    dialog.qr = QLabel()
    dialog.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(dialog.qr)
    dialog.address = QComboBox()
    for address in addresses:
        dialog.address.addItem(address, address)
    row = QHBoxLayout()
    row.addWidget(QLabel("Netzwerkadresse"))
    row.addWidget(dialog.address, 1)
    layout.addLayout(row)
    dialog.address.setVisible(len(addresses) > 1)
    row.itemAt(0).widget().setVisible(len(addresses) > 1)
    dialog.link = QLineEdit()
    dialog.link.setReadOnly(True)
    copy = QPushButton("Link kopieren")
    copy.clicked.connect(lambda: QApplication.clipboard().setText(dialog.link.text()))
    link_row = QHBoxLayout()
    link_row.addWidget(dialog.link, 1)
    link_row.addWidget(copy)
    layout.addLayout(link_row)
    dialog.status = QLabel()
    layout.addWidget(dialog.status)
    hint = QLabel("Die Freigabe endet beim Schließen, spätestens nach 15 Minuten. Fragt die Windows-Firewall nach, "
                  "den Zugriff für „Private Netzwerke“ erlauben.")
    hint.setObjectName("muted")
    hint.setWordWrap(True)
    layout.addWidget(hint)
    close = QPushButton("Schließen")
    close.clicked.connect(dialog.accept)
    layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
    dialog.share = None

    def start(address):
        if dialog.share:
            dialog.share.stop()
        dialog.share = Share(files, address)
        dialog.link.setText(dialog.share.url)
        dialog.link.setCursorPosition(0)
        pix = QPixmap.fromImage(pil_to_qimage(qr_image(dialog.share.url, 280).convert("RGB")))
        dialog.qr.setPixmap(pix)
        refresh()

    def refresh():
        if dialog.share:
            dialog.status.setText(status_text(dialog.share))

    def finished(*_):
        timer.stop()
        if dialog.share:
            dialog.share.stop()
        if cleanup:
            cleanup()

    timer = QTimer(dialog)
    timer.timeout.connect(refresh)
    timer.start(500)
    dialog.address.currentIndexChanged.connect(lambda _: start(dialog.address.currentData()))
    dialog.finished.connect(finished)
    start(addresses[0])
    return dialog
