"""Exercise the native Windows PrintScreen hook without taking a screenshot."""
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from screenkaffee.hotkeys import WindowsHotkeys


def main():
    if sys.platform != "win32":
        raise SystemExit("Dieser Test benötigt Windows.")
    app = QApplication([])
    calls = []
    keys = WindowsHotkeys({"region": lambda: calls.append("region")})
    try:
        assert keys.bind({"region": "Print"}) == {}
        assert keys.hook, "Keyboard-Hook konnte nicht installiert werden"
        def press():
            user = keys.ctypes.windll.user32
            user.keybd_event(0x2C, 0, 0, 0)
            user.keybd_event(0x2C, 0, 2, 0)
        QTimer.singleShot(100, press)
        QTimer.singleShot(500, app.quit)
        app.exec()
        assert calls == ["region"], calls
    finally:
        keys.close()
    print("Windows-Druck-Hook: genau ein Aufruf, keine Aufnahme, Hook freigegeben.")


if __name__ == "__main__":
    main()
