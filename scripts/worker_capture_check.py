"""Exercise the real asynchronous capture path and GUI-thread delivery."""
import tempfile
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QThread
from screenkaffee.app import MainWindow
from screenkaffee.workspace import Workspace
from screenkaffee.windows_capture import capture_monitors

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
folder = tempfile.TemporaryDirectory(prefix="prism-worker-")
window = MainWindow(Workspace(Path(folder.name)))
result = {"ok": False}


def finished(monitors):
    result["ok"] = bool(monitors) and QThread.currentThread() == app.thread()
    print("Worker capture:", result["ok"], [m.metadata for m in monitors], flush=True)


def check_finished():
    if not window.tasks:
        timer.stop()
        if window.hotkeys:
            window.hotkeys.close()
        window.tray.hide()
        window.workspace.close()
        app.quit()


window.task(capture_monitors, finished, "Test")
timer = QTimer()
timer.timeout.connect(check_finished)
timer.start(100)
QTimer.singleShot(15000, app.quit)
app.exec()
raise SystemExit(0 if result["ok"] else 1)
