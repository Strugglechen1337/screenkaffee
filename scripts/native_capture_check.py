"""Show only a synthetic test chart; compare native capture against known pixels."""
import ctypes
import json
from pathlib import Path
ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QPainter, QColor
from PySide6.QtCore import Qt, QPoint
from screenkaffee.windows_capture import capture_monitors, capture_window

colors = [(0, 0, 0), (255, 255, 255), (128, 128, 128), (50, 100, 150), (230, 90, 50), (70, 180, 120)]


class Chart(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        for index, color in enumerate(colors):
            painter.fillRect(index * 100, 0, 100, 240, QColor(*color))


app = QApplication([])
chart = Chart()
chart.setWindowTitle("screenkaffee Farbtest")
chart.resize(600, 240)
chart.move(100, 100)
chart.show()
chart.raise_()
app.processEvents()
import time
time.sleep(.3)
captures = capture_monitors()
origin = chart.mapToGlobal(QPoint(0, 0))
scale = chart.devicePixelRatioF()
samples = []
for index, expected in enumerate(colors):
    x, y = round((origin.x() + 50 + index * 100) * scale), round((origin.y() + 100) * scale)
    monitor = next(m for m in captures if m.bounds[0] <= x < m.bounds[2] and m.bounds[1] <= y < m.bounds[3])
    actual = monitor.image.getpixel((x - monitor.bounds[0], y - monitor.bounds[1]))
    error = max(abs(a - b) for a, b in zip(actual, expected))
    samples.append({"expected": expected, "actual": actual, "max_channel_error": error})
window = capture_window(int(chart.winId()))
window_samples = []
for index, expected in enumerate(colors):
    x, y = round((origin.x() + 50 + index * 100) * scale), round((origin.y() + 100) * scale)
    actual = window.image.getpixel((x - window.bounds[0], y - window.bounds[1]))
    window_samples.append({"expected": expected, "actual": actual,
        "max_channel_error": max(abs(a - b) for a, b in zip(actual, expected))})
chart.close()
report = {"backend": captures[0].metadata, "patches": samples, "window_patches": window_samples,
          "passed": all(s["max_channel_error"] <= 3 for s in samples + window_samples)}
Path(".tools/native-capture-check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
raise SystemExit(0 if report["passed"] else 1)
