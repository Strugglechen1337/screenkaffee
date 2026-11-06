import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import sys


def main():
    if "--apply-update" in sys.argv[1:]:          # neues Paket tauscht die alte Version aus (siehe updater.py)
        from .updater import run_helper
        return run_helper(sys.argv[1:])
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.ole32.OleInitialize(None)
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    from .cli import dispatch
    code = dispatch(sys.argv[1:])
    if code is not None:
        return code
    parser = argparse.ArgumentParser(description="screenkaffee — portable screenshot workspace",
        epilog="Weitere Befehle: 'capture --screenshotregion --rect X;Y;W;H --filepath Datei' und 'videos --merge A B' (ohne Fenster).")
    parser.add_argument("files", nargs="*", type=Path, help="Bilder, die im Editor geöffnet werden (Explorer: Öffnen mit)")
    from .hotkeys import ACTIONS
    parser.add_argument("--capture", choices=[action for action, _ in ACTIONS], help="Aufnahme oder Videoaktion auslösen (für Hyprland-Hotkeys)")
    parser.add_argument("--data-dir", type=Path, help="Abweichender Datenordner")
    parser.add_argument("--diagnose", action="store_true", help="Systemdaten anzeigen; keine Bilddaten speichern")
    parser.add_argument("--minimized", action="store_true", help="Ohne Fenster im Infobereich starten (Autostart)")
    parser.add_argument("--self-test", action="store_true", help="Portablen Start ohne Anzeige testen")
    parser.add_argument("--speech-test", action="store_true", help="Mit --self-test: auch die Untertitel-Erweiterung laden und prüfen")
    parser.add_argument("--newinstance", action="store_true", help="Neue Instanz starten und die laufende beenden")
    parser.add_argument("--multiinstances", action="store_true", help="Weitere Instanz starten, ohne nach einer laufenden zu suchen (mit Vorsicht)")
    parser.add_argument("--policies", nargs="?", const="", metavar="ORDNER", help="ADMX-, ADML- und REG-Vorlagen für Administratoren schreiben (Standard: Desktop)")
    args = parser.parse_args()
    if args.policies is not None:
        from .policy import write_policy_templates
        folder = Path(args.policies) if args.policies else Path(os.environ.get("USERPROFILE") or Path.home()) / "Desktop"
        for path in write_policy_templates(folder):
            print(path)
        return 0
    if args.data_dir:
        os.environ["SCREENKAFFEE_DATA"] = str(args.data_dir.resolve())
    if args.self_test:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from PySide6.QtNetwork import QLocalSocket, QLocalServer
    from .workspace import Workspace, portable_root
    from .app import MainWindow, STYLE, app_icon
    app = QApplication(sys.argv[:1])
    app.setApplicationName("screenkaffee")
    app.setOrganizationName("screenkaffee")
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setWindowIcon(app_icon())
    if args.diagnose:
        from .integrations import tool
        report = {"platform": sys.platform, "python": sys.version, "data": str(portable_root()),
            "session": os.environ.get("XDG_SESSION_TYPE"),
            "hyprland": bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")),
            "screens": [{"name": s.name(), "size": [s.size().width(), s.size().height()], "dpr": s.devicePixelRatio()} for s in app.screens()],
            "tools": {name: tool(name) for name in ["grim", "slurp", "hyprctl", "tesseract", "adb", "ffmpeg", "wf-recorder"]}}
        if sys.platform == "win32":
            try:
                from .windows_capture import capture_monitors
                report["capture"] = [dict(m.metadata, size=m.image.size) for m in capture_monitors()]
            except Exception as exc:
                report["capture_error"] = str(exc)
        text = json.dumps(report, ensure_ascii=False, indent=2)
        path = (args.data_dir or portable_root()) / "diagnostics.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if sys.stdout:
            print(text)
        return 1 if report.get("capture_error") else 0
    workspace = Workspace()
    logging.basicConfig(filename=workspace.root / "screenkaffee.log", level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    crash_dialogs = []
    def exception_hook(kind, value, tb):
        logging.error("Unbehandelter Fehler", exc_info=(kind, value, tb))
        if any(dialog.isVisible() for dialog in crash_dialogs):    # Folgefehler nicht als Fensterflut zeigen
            return
        from .crash import crash_report, show_crash_dialog
        crash_dialogs[:] = [show_crash_dialog(crash_report(kind, value, tb), workspace.root / "screenkaffee.log")]
    sys.excepthook = exception_hook
    if args.self_test:
        from .editor import EditorView
        from PIL import Image
        view = EditorView(Image.new("RGB", (100, 80), "#336699"))
        assert view.render_image().getpixel((20, 20)) == (51, 102, 153)
        # Die Pakete sind ausgedünnt (scripts/package_prune.py): alles laden, was die App an Qt nutzt.
        from PySide6.QtGui import QImageReader
        from PySide6.QtMultimedia import QMediaDevices, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        from PySide6.QtNetwork import QLocalServer
        from PySide6.QtPrintSupport import QPrinter
        player, video = QMediaPlayer(), QVideoWidget()
        player.setVideoOutput(video)
        assert player.isAvailable(), "Qt-Multimedia-Backend fehlt"
        assert QMediaDevices is not None and QLocalServer is not None and QPrinter is not None
        formats = {bytes(name).decode() for name in QImageReader.supportedImageFormats()}
        assert {"png", "jpg", "svg"} <= formats, formats
        from .lanshare import qr_image
        from .compare import difference
        import http.server  # noqa: F401  (für „Aufs Handy senden“)
        assert qr_image("screenkaffee").width > 20 and difference(qr_image("a"), qr_image("a")).ratio == 0
        from .beautify import beautify
        assert beautify(Image.new("RGB", (60, 40)), frame="mac", title="Test").size[0] > 60
        from .cleanup import dhash
        from .ruler import measurement
        from .steps import mark_click
        from .templates import builtin_templates
        assert dhash(Image.new("RGB", (40, 30))) == 0 and measurement(0, 0, 3, 4)["length"] == 5
        assert mark_click(Image.new("RGB", (80, 60)), 40, 30, 1).size == (80, 60) and builtin_templates()
        if args.speech_test:
            from .subtitles import self_test_speech
            self_test_speech()
            (portable_root() / "self-test-speech-ok.txt").write_text("Untertitel-Erweiterung OK\n", encoding="utf-8")
        workspace.close()
        (portable_root() / "self-test-ok.txt").write_text("Portable runtime, Qt, Pillow and editor OK\n", encoding="utf-8")
        return 0
    server_name = "screenkaffee-" + hashlib.sha256(str(workspace.root.resolve()).encode()).hexdigest()[:20]
    if args.multiinstances:
        server_name += f"-{os.getpid()}"            # a private channel: never talks to or replaces another instance
    elif args.newinstance:
        from .cli import ask_to_quit
        ask_to_quit(server_name)
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    if socket.waitForConnected(500):
        if args.files:
            socket.write(b"".join(b"edit:" + str(path.resolve()).encode() + b"\n" for path in args.files))
        else:
            socket.write((args.capture or "show").encode() + b"\n")
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        workspace.close()
        return 0
    server = QLocalServer()
    QLocalServer.removeServer(server_name)
    if not server.listen(server_name):
        raise RuntimeError("Lokaler Steuerkanal konnte nicht geöffnet werden.")
    window = MainWindow(workspace)
    clients = []
    def new_connection():
        while server.hasPendingConnections():
            client = server.nextPendingConnection()
            clients.append(client)
            def receive(client=client):
                if not client.canReadLine():
                    return
                while client.canReadLine():
                    command = bytes(client.readLine()).decode().strip()
                    if command in {action for action, _ in ACTIONS}:
                        window.command_action(command)
                    elif command.startswith("edit:"):
                        window.open_external_image(command[5:])
                    elif command == "quit":
                        QTimer.singleShot(0, window.quit_app)
                    else:
                        window.restore()
                client.disconnectFromServer()
            client.readyRead.connect(receive)
            client.disconnected.connect(client.deleteLater)
            client.disconnected.connect(lambda client=client: clients.remove(client) if client in clients else None)
            receive()
    server.newConnection.connect(new_connection)
    try:
        from . import autostart
        autostart.refresh()
    except OSError:
        logging.warning("Autostart-Eintrag konnte nicht aktualisiert werden", exc_info=True)
    if args.files:
        for path in args.files:
            window.open_external_image(str(path.resolve()))
    elif args.minimized and window.tray.isVisible():
        pass
    elif args.minimized:
        window.showMinimized()
    else:
        window.show()
    if args.capture:
        QTimer.singleShot(200, lambda: window.command_action(args.capture))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
