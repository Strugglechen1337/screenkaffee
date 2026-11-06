"""Full-page screenshot of a URL through a locally installed Chrome/Edge (DevTools protocol).

Pages are rendered by the user's own browser; nothing is sent to any service by this module.
"""
import base64
import io
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from PIL import Image

MAX_HEIGHT = 30000


def find_browser():
    names = ["chrome", "msedge", "chromium", "google-chrome", "chromium-browser", "microsoft-edge"]
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "win32":
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            for rel in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"):
                if base and (Path(base) / rel).is_file():
                    return str(Path(base) / rel)
    return None


class WebSocket:
    """Minimal client for ws://host:port/path (text frames only)."""
    def __init__(self, url, timeout=30):
        host, _, rest = url[len("ws://"):].partition("/")
        name, _, port = host.partition(":")
        self.sock = socket.create_connection((name, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{rest} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise ConnectionError("WebSocket-Handshake fehlgeschlagen")
            response += chunk
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise ConnectionError("WebSocket-Handshake abgelehnt")
        self.buffer = response.split(b"\r\n\r\n", 1)[1]

    def send(self, text):
        data = text.encode()
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(bytes(header) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _read(self, n):
        while len(self.buffer) < n:
            chunk = self.sock.recv(1 << 16)
            if not chunk:
                raise ConnectionError("Browser hat die Verbindung beendet")
            self.buffer += chunk
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def receive(self):
        message = b""
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            if b1 & 0x0F == 8:
                raise ConnectionError("Browser hat die Verbindung geschlossen")
            if b1 & 0x0F in (0, 1, 2):
                message += payload
                if b1 & 0x80:
                    return message.decode()

    def close(self):
        self.sock.close()


class Devtools:
    def __init__(self, ws):
        self.ws, self.next_id = ws, 0

    def call(self, method, **params):
        self.next_id += 1
        self.ws.send(json.dumps({"id": self.next_id, "method": method, "params": params}))
        while True:
            message = json.loads(self.ws.receive())
            if message.get("id") == self.next_id:
                if "error" in message:
                    raise RuntimeError(message["error"].get("message", "DevTools-Fehler"))
                return message.get("result", {})

    def wait_event(self, name, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.ws.sock.settimeout(max(.1, deadline - time.monotonic()))
            try:
                message = json.loads(self.ws.receive())
            except socket.timeout:
                return False
            if message.get("method") == name:
                return True
        return False


def capture_webpage(url, width=1280, browser=None, timeout=45, settle=1.0):
    if not url.startswith(("http://", "https://", "file://")):
        raise ValueError("Nur http-, https- und file-Adressen werden unterstützt.")
    browser = browser or find_browser()
    if not browser:
        raise RuntimeError("Für Webseiten-Aufnahmen wird Chrome oder Edge benötigt.")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    profile = tempfile.mkdtemp(prefix="screenkaffee-web-")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen([browser, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
        "--hide-scrollbars", "--no-first-run", "--no-default-browser-check", "--disable-gpu", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    ws = None
    try:
        deadline = time.monotonic() + 20
        target = None
        while time.monotonic() < deadline and not target:
            try:
                pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2))
                target = next((p["webSocketDebuggerUrl"] for p in pages if p.get("type") == "page"), None)
            except OSError:
                time.sleep(.2)
        if not target:
            raise RuntimeError("Der Browser konnte nicht gestartet werden.")
        ws = WebSocket(target, timeout=timeout)
        cdp = Devtools(ws)
        cdp.call("Page.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", width=int(width), height=800, deviceScaleFactor=1, mobile=False)
        cdp.call("Page.navigate", url=url)
        if not cdp.wait_event("Page.loadEventFired", timeout):
            raise RuntimeError("Die Seite wurde nicht rechtzeitig geladen.")
        time.sleep(settle)
        size = cdp.call("Page.getLayoutMetrics")["cssContentSize"]
        height = min(MAX_HEIGHT, max(1, int(size["height"])))
        cdp.call("Emulation.setDeviceMetricsOverride", width=int(width), height=height, deviceScaleFactor=1, mobile=False)
        shot = cdp.call("Page.captureScreenshot", format="png", captureBeyondViewport=True,
                        clip={"x": 0, "y": 0, "width": int(width), "height": height, "scale": 1})
        with Image.open(io.BytesIO(base64.b64decode(shot["data"]))) as image:
            return image.convert("RGB")
    finally:
        if ws:
            ws.close()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(profile, ignore_errors=True)
