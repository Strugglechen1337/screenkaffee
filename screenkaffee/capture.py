"""Native-coordinate region capture and explicitly initiated scrolling capture."""
import ctypes
from dataclasses import dataclass
import io
import math
import os
import sys
import threading
import time
import numpy as np
from PIL import Image
from .integrations import tool, run
from .stitch import overlap_rows
from .i18n import tr


def capture_region(bounds, tone_mapping="neutral", cursor=False):
    left, top, width, height = map(int, bounds)
    if min(width, height) < 2 or width * height > 150_000_000:
        raise ValueError(tr("Ungültiger Aufnahmebereich."))
    if sys.platform == "win32":
        from .windows_capture import capture_monitors
        try:
            monitors = capture_monitors(tone_mapping, cursor=cursor, target=(left, top, left + width, top + height))
        except TypeError:
            monitors = capture_monitors(tone_mapping, cursor=cursor)
        image = Image.new("RGB", (width, height))
        intersections = []
        for monitor in monitors:
            ml, mt, mr, mb = monitor.bounds
            x1, y1, x2, y2 = max(left, ml), max(top, mt), min(left + width, mr), min(top + height, mb)
            if x2 > x1 and y2 > y1:
                image.paste(monitor.image.crop((x1 - ml, y1 - mt, x2 - ml, y2 - mt)), (x1 - left, y1 - top))
                intersections.append(monitor.metadata)
        if not intersections:
            raise ValueError(tr("Bereich liegt außerhalb der aktiven Monitore."))
        return image, {"backend": "Windows Graphics Capture", "region": list(bounds), "monitors": intersections}
    from .linux_capture import is_wayland
    if not is_wayland():
        from PySide6.QtGui import QGuiApplication, QImage
        app = QGuiApplication.instance()
        if not app or app.platformName() != "xcb":
            raise RuntimeError(tr("X11-Aufnahme benötigt eine aktive Qt-XCB-Sitzung."))
        if cursor:
            raise RuntimeError(tr("X11-Aufnahme mit Mauszeiger wird nicht unterstützt."))
        screens = app.screens()
        if not screens or any(screen.devicePixelRatio() != 1 for screen in screens):
            raise RuntimeError(tr("X11-Aufnahme mit Qt-Bildschirmskalierung wird nicht unterstützt."))
        screen = app.primaryScreen()
        geometry = screen.virtualGeometry()
        pixmap = screen.grabWindow(0)
        if pixmap.isNull():
            raise RuntimeError(tr("X11-Bildschirmaufnahme fehlgeschlagen."))
        qimage = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        if (qimage.width(), qimage.height()) != (geometry.width(), geometry.height()):
            raise RuntimeError(tr("X11-Bildschirmgeometrie stimmt nicht mit der Aufnahme überein."))
        desktop = Image.frombytes("RGBA", (qimage.width(), qimage.height()), bytes(qimage.bits()), "raw", "RGBA", qimage.bytesPerLine()).convert("RGB")
        x, y = left - geometry.x(), top - geometry.y()
        if x >= desktop.width or y >= desktop.height or x + width <= 0 or y + height <= 0:
            raise ValueError(tr("Bereich liegt außerhalb der aktiven Monitore."))
        return desktop.crop((x, y, x + width, y + height)), {"backend": "X11 / Qt", "region": list(bounds), "hdr_validated": False}
    grim = tool("grim")
    if not grim:
        raise RuntimeError(tr("Feste Bereiche unter Wayland benötigen grim."))
    args = [grim, "-t", "png", "-g", f"{left},{top} {width}x{height}"]
    if cursor:
        args.append("-c")
    result = run([*args, "-"])
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    from .color import import_srgb
    return import_srgb(io.BytesIO(result.stdout)), {"backend": "Hyprland / grim", "region": list(bounds), "hdr_validated": False}


def scroll_down(bounds, amount=3, direction="vertical"):
    left, top, width, height = bounds
    if sys.platform == "win32":
        # The user picked this target and initiated automatic scrolling.
        api = ctypes.WinDLL("user32")
        api.SetCursorPos(round(left + width / 2), round(top + height / 2))
        class MouseInput(ctypes.Structure):
            _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32), ("data", ctypes.c_uint32),
                        ("flags", ctypes.c_uint32), ("time", ctypes.c_uint32), ("extra", ctypes.c_size_t)]
        class InputUnion(ctypes.Union):
            _fields_ = [("mouse", MouseInput), ("padding", ctypes.c_byte * 32)]
        class Input(ctypes.Structure):
            _fields_ = [("type", ctypes.c_uint32), ("value", InputUnion)]
        event = Input(0, InputUnion(mouse=MouseInput(0, 0, ((120 if direction == "horizontal" else -120) * amount) & 0xffffffff,
            0x1000 if direction == "horizontal" else 0x0800, 0, 0)))
        api.SendInput.argtypes = [ctypes.c_uint32, ctypes.POINTER(Input), ctypes.c_int]
        if api.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
            raise RuntimeError(tr("Scrollen wurde vom Zielprogramm blockiert."))
    else:
        hyprctl = tool("hyprctl")
        if not hyprctl:
            raise RuntimeError(tr("Automatisches Scrollen benötigt Hyprland / hyprctl."))
        key = "Right" if direction == "horizontal" else "Next"
        result = run([hyprctl, "dispatch", "sendshortcut", f",{key},activewindow"])
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace"))


@dataclass
class ScrollResult:
    image: Image.Image
    frames: int
    stop_reason: str


def scroll_capture(bounds, tone_mapping="neutral", max_frames=40, pause=.45,
                   cancel=None, progress=None, capture=None, scroll=None, direction="vertical", header=0, footer=0):
    if not 2 <= max_frames <= 200 or not .1 <= pause <= 5:
        raise ValueError(tr("Ungültige Scrollparameter."))
    capture = capture or (lambda: capture_region(bounds, tone_mapping)[0])
    scroll = scroll or (lambda: scroll_down(bounds, direction=direction))
    cancel = cancel or threading.Event()
    first = capture()
    from .stitch import scroll_body, restore_scroll
    last = first
    body = scroll_body(first, direction, header, footer)[1]
    result, previous, count = body.copy(), body, 1
    reason = tr("Aufnahmelimit erreicht")
    for _ in range(max_frames - 1):
        if cancel.is_set():
            reason = tr("Vom Nutzer beendet")
            break
        scroll()
        if cancel.wait(pause):
            reason = tr("Vom Nutzer beendet")
            break
        frame = capture()
        raw = frame
        frame = scroll_body(frame, direction, header, footer)[1]
        if frame.size != previous.size:
            raise RuntimeError(tr("Der Aufnahmebereich hat seine Größe geändert."))
        difference = np.asarray(frame, dtype=np.int16) - np.asarray(previous, dtype=np.int16)
        if float(np.abs(difference).mean()) < .6:
            reason = tr("Ende des Scrollbereichs erreicht")
            break
        rows = overlap_rows(previous, frame)
        extra = frame.height - rows
        if (result.height + extra) * result.width > 150_000_000:
            reason = tr("Bildgrößenlimit erreicht")
            break
        canvas = Image.new("RGB", (result.width, result.height + extra))
        canvas.paste(result)
        canvas.paste(frame.crop((0, rows, frame.width, frame.height)), (0, result.height))
        result, previous = canvas, frame
        last = raw
        count += 1
        if progress:
            progress(count, result.height)
    return ScrollResult(restore_scroll(result, first, last, direction, header, footer), count, reason)
