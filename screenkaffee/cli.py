"""Command line without a window: "capture --screenshotregion --rect X;Y;W;H --filepath F" and "videos --merge A B".

Both run without a window and exit with 0 on success. "-1;-1;-1;-1" means the whole desktop.
"""
import argparse
import sys
from pathlib import Path


def parse_rect(text):
    """"X;Y;W;H" -> (x, y, w, h); "-1;-1;-1;-1" -> None (whole desktop)."""
    try:
        values = [int(part) for part in str(text).replace(",", ";").split(";")]
    except ValueError:
        raise ValueError("Ungültiges Rechteck. Erwartet: X;Y;Breite;Höhe") from None
    if len(values) != 4:
        raise ValueError("Ungültiges Rechteck. Erwartet: X;Y;Breite;Höhe")
    if values == [-1, -1, -1, -1]:
        return None
    if values[2] < 2 or values[3] < 2:
        raise ValueError("Breite und Höhe des Rechtecks müssen mindestens 2 Pixel betragen.")
    return tuple(values)


def desktop_bounds():
    from PySide6.QtGui import QGuiApplication
    screen = QGuiApplication.primaryScreen()
    geometry = screen.virtualGeometry()
    scale = screen.devicePixelRatio()
    return (round(geometry.x() * scale), round(geometry.y() * scale), round(geometry.width() * scale), round(geometry.height() * scale))


def cli_policy(*features):
    """Richtlinien vor jeder Aufnahme und Medienaktion prüfen."""
    import json
    from .policy import load_policy, apply_policy
    from .workspace import portable_root
    policy = load_policy()
    for feature in features:
        if feature in policy["disabled"]:
            raise RuntimeError(f"Die Funktion {feature} ist durch eine Richtlinie gesperrt.")
    settings = {}
    try:
        data = json.loads((portable_root() / "settings.json").read_text(encoding="utf-8"))
        if isinstance(data, dict):
            settings.update(data)
    except (OSError, ValueError):
        pass
    return apply_policy(settings, policy)


def run_video(args, recorder_factory=None, sleep=None, clock=None):
    """Record a fixed region without a window until --delay minutes have passed (default 60) or Ctrl+C."""
    import shutil
    import tempfile
    import time
    settings = cli_policy("capture", "capture.cli", "video")
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    if args.rect is None:
        raise SystemExit("Für Videos wird --rect X;Y;Breite;Höhe benötigt (-1;-1;-1;-1 = ganzer Desktop).")
    bounds = parse_rect(args.rect)
    minutes = 60 if args.delay is None else args.delay
    if not 0 < minutes <= 600:
        raise ValueError("--delay (Höchstdauer in Minuten) muss zwischen 0 und 600 liegen.")
    if recorder_factory is None:
        from .integrations import VideoRecorder as recorder_factory
    recorder = recorder_factory()
    folder = Path(tempfile.mkdtemp(prefix="screenkaffee-video-"))
    try:
        try:
            recorder.start(folder, region=list(bounds) if bounds else None,
                           fps=settings.get("video_fps", 30), quality=settings.get("video_quality", "high"))
            deadline = clock() + minutes * 60
            try:
                while clock() < deadline:
                    sleep(min(.5, max(0, deadline - clock())))
                    error = recorder.error_if_stopped()
                    if error:
                        raise RuntimeError(error)
            except KeyboardInterrupt:
                pass
        except BaseException:
            # Die ursprüngliche Ursache melden; ein Folgefehler beim Stoppen darf sie nicht verdecken.
            try:
                recorder.stop()
            except Exception as stop_error:
                print(f"Beim Stoppen der Aufnahme trat zusätzlich ein Fehler auf: {stop_error}", file=sys.stderr)
            raise
        result = recorder.stop()
        if not result:
            raise RuntimeError("Die Aufnahme lieferte kein Video.")
        target = args.filepath or Path.cwd() / Path(result).name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result, target)
    except BaseException:
        print(f"Aufnahmedaten zur Wiederherstellung bleiben erhalten: {folder}", file=sys.stderr)
        raise
    shutil.rmtree(folder)
    print(target)
    return 0


def run_capture(argv, capture=None, recorder_factory=None, sleep=None, clock=None):
    parser = argparse.ArgumentParser(prog="screenkaffee capture", description="Bereich ohne Fenster aufnehmen und speichern.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--screenshotregion", "-screenshotregion", action="store_true")
    mode.add_argument("--videoregion", "-videoregion", action="store_true")
    parser.add_argument("--rect", "-rect", help="X;Y;Breite;Höhe, -1;-1;-1;-1 = ganzer Desktop")
    parser.add_argument("--filepath", "-filepath", type=Path, help="Zieldatei (Bild: PNG, JPEG, WebP, TIFF, BMP, GIF; Video: MP4)")
    parser.add_argument("--delay", "-delay", type=float, help="Videoaufnahme: Höchstdauer in Minuten (Standard 60)")
    argv = list(argv)
    for index, token in enumerate(argv[:-1]):
        if token in ("--rect", "-rect") and argv[index + 1].startswith("-"):
            argv[index:index + 2] = [f"--rect={argv[index + 1]}"]  # argparse would read "-1;-1;-1;-1" as an option
            break
    args = parser.parse_args(argv)
    if args.videoregion:
        return run_video(args, recorder_factory, sleep, clock)
    settings = cli_policy("capture", "capture.cli", "capture.region")
    bounds = parse_rect(args.rect) if args.rect else None
    if args.rect is None:
        raise SystemExit("Ohne --rect wird der Bereich interaktiv gewählt: screenkaffee --capture region")
    if capture is None:
        from .capture import capture_region
        capture = lambda bounds: capture_region(bounds, settings.get("tone_mapping", "neutral"), settings.get("capture_cursor", False))
    image, metadata = capture(bounds or desktop_bounds())
    if args.filepath:
        from .color import save_image
        args.filepath.parent.mkdir(parents=True, exist_ok=True)
        save_image(image, args.filepath, metadata={"Software": "screenkaffee"})
        print(args.filepath)
    else:
        from .workspace import Workspace
        workspace = Workspace()
        ident = workspace.add(image, metadata=metadata)
        print(workspace.path(ident))
        workspace.close()
    return 0


def run_videos(argv):
    cli_policy("video", "videos")
    parser = argparse.ArgumentParser(prog="screenkaffee videos", description="Videos mit gleichem Format zusammenfügen.")
    parser.add_argument("--merge", "-merge", nargs="+", type=Path, required=True, metavar="DATEI",
                        help="Zwei oder mehr Videos; die Ausgabe landet neben dem ersten Video")
    parser.add_argument("--output", "-output", type=Path, help="Zieldatei (Standard: <erstes>-kombiniert.mp4)")
    args = parser.parse_args(argv)
    if len(args.merge) < 2:
        raise SystemExit("Zum Zusammenfügen werden mindestens zwei Videos benötigt.")
    from .media import merge_videos
    output = args.output or args.merge[0].with_name(args.merge[0].stem + "-kombiniert.mp4")
    print(merge_videos(args.merge, output))
    return 0


def ask_to_quit(server_name, wait_ms=5000):
    """--newinstance: ask a running instance to quit and wait until its control channel is gone."""
    import time
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalSocket
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    if not socket.waitForConnected(500):
        return False
    socket.write(b"quit\n")
    socket.waitForBytesWritten(1000)
    socket.disconnectFromServer()
    deadline = time.monotonic() + wait_ms / 1000
    while time.monotonic() < deadline:
        if QCoreApplication.instance():
            QCoreApplication.processEvents()
        probe = QLocalSocket()
        probe.connectToServer(server_name)
        if not probe.waitForConnected(200):
            return True
        probe.abort()
        time.sleep(.05)
    return False


COMMANDS = {"capture": run_capture, "videos": run_videos}


def dispatch(argv):
    """Returns an exit code when argv starts with a capture/videos sub-command, otherwise None."""
    if argv and argv[0] in COMMANDS:
        if argv[0] == "capture":
            import os
            from PySide6.QtWidgets import QApplication
            QApplication.instance() or QApplication(sys.argv[:1])
        try:
            return COMMANDS[argv[0]](argv[1:])
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"Fehler: {exc}", file=sys.stderr)
            return 1
    return None
