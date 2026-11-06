"""Regressionstests zu den Review-Funden nach 1.1.0 (Freigabe, Zeitraffer, Video-Editor, Miniaturen, Editor, CLI)."""
import socket
import threading
import time
import urllib.request

import pytest
from PIL import Image

from screenkaffee import lanshare, timelapse


def wait_until(condition, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not condition():
        time.sleep(0.02)
    return condition()


def half_request(address):
    """Verbindung mit unvollständigem HTTP-Kopf (es fehlt die abschließende Leerzeile)."""
    client = socket.create_connection(address, timeout=5)
    client.sendall(b"GET / HTTP/1.1\r\nHost: test\r\n")
    return client


def closed_by_server(client, seconds=5.0):
    client.settimeout(seconds)
    try:
        return client.recv(1024) == b""
    except (ConnectionResetError, ConnectionAbortedError):
        return True
    except TimeoutError:
        return False


# --- A) LAN-Freigabe ---------------------------------------------------------------------------------------------

def test_share_stop_disconnects_half_open_connections(tmp_path):
    picture = tmp_path / "bild.png"
    Image.new("RGB", (4, 4), "red").save(picture)
    share = lanshare.Share([(picture, "x")], address="127.0.0.1", request_timeout=60)
    clients = []
    try:
        clients = [half_request(share.server.server_address) for _ in range(3)]
        assert wait_until(lambda: len(share.server.active) == 3)
        threads = list(share.server.active.values())
        share.stop()
        assert not any(thread.is_alive() for thread in threads)
        assert not share.server.active
        assert all(closed_by_server(client, 2) for client in clients)
    finally:
        share.stop()
        for client in clients:
            client.close()


def test_share_limits_parallel_connections_and_times_out_idle_ones(tmp_path):
    picture = tmp_path / "bild.png"
    Image.new("RGB", (4, 4), "red").save(picture)
    share = lanshare.Share([(picture, "x")], address="127.0.0.1", max_connections=2, request_timeout=0.5)
    clients = []
    try:
        clients = [half_request(share.server.server_address) for _ in range(2)]
        assert wait_until(lambda: len(share.server.active) == 2)
        extra = half_request(share.server.server_address)
        clients.append(extra)
        assert closed_by_server(extra, 2)                                     # dritte Verbindung sofort abgewiesen
        assert len(share.server.active) == 2
        # Ohne weitere Daten trennt das Zeitlimit die halben Verbindungen, danach geht ein normaler Abruf wieder.
        assert all(closed_by_server(client, 3) for client in clients[:2])
        assert wait_until(lambda: not share.server.active)
        with urllib.request.urlopen(share.url, timeout=5) as response:
            assert response.status == 200
    finally:
        share.stop()
        for client in clients:
            client.close()


# --- B) Zeitraffer -------------------------------------------------------------------------------------------------

def test_timelapse_keeps_frames_when_the_capture_thread_hangs(tmp_path, monkeypatch):
    blocked, release = threading.Event(), threading.Event()
    calls = []

    def grab():
        calls.append(1)
        if len(calls) >= 3:
            blocked.set()
            release.wait(10)
        return Image.new("RGB", (20, 20), "blue")

    rendered = []
    import screenkaffee.media as media
    monkeypatch.setattr(media, "_render", lambda *args: rendered.append(args))
    lapse = timelapse.Timelapse(tmp_path, grab, interval=0.01)
    lapse.start()
    try:
        assert blocked.wait(5)
        with pytest.raises(RuntimeError, match="reagiert nicht") as error:
            lapse.render(timeout=0.2)
        assert str(lapse.folder) in str(error.value)
        assert not rendered and lapse.folder.exists() and lapse.active
        assert len(list(lapse.folder.glob("frame_*.jpg"))) == 2
    finally:
        release.set()
        lapse.thread.join(5)
    assert lapse.last_error is None and lapse.frames == 3                    # das späte Bild landet im Ordner
    assert lapse.stop(0.1) is True


# --- C) Zuschnitt-Felder im Video-Editor ----------------------------------------------------------------------------

@pytest.fixture
def small_video(tmp_path):
    import subprocess
    from screenkaffee.media import ffmpeg_binary
    path = tmp_path / "klein.mp4"
    subprocess.run([ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=d=1:s=640x360:r=10", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_video_crop_fields_accept_typed_numbers(qt_app, small_video):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from screenkaffee.media_editor import VideoEditor
    editor = VideoEditor(small_video)
    editor.show()
    qt_app.processEvents()
    x, y, w, h = editor.crop
    frames = []
    for spin, text, expected in ((w, "600", 600), (x, "15", 14), (h, "300", 300)):
        spin.setFocus()
        spin.selectAll()
        QTest.keyClicks(spin, text)
        frames.append(editor.crop_widget.crop_rect)                           # Rahmen folgt schon beim Tippen
        QTest.keyClick(spin, Qt.Key.Key_Tab)
        qt_app.processEvents()
        assert spin.value() == expected, text
    assert frames[0][2] == 600 and frames[2][3] == 300
    assert editor.crop_values() == (14, 0, 600, 300) == editor.crop_widget.crop_rect
    # Unbestätigte Eingabe wird beim Export übernommen (und begrenzt).
    y.setFocus()
    y.selectAll()
    QTest.keyClicks(y, "61")
    assert editor.crop_values() == (14, 60, 600, 300)
    editor.close()


# --- D/E/F) Miniaturen ---------------------------------------------------------------------------------------------

def main_window(workspace, monkeypatch):
    from screenkaffee.app import MainWindow
    monkeypatch.setattr(MainWindow, "apply_hotkeys", lambda self: {})
    return MainWindow(workspace)


def drain(loader):
    for _ in range(500):
        if not loader.jobs:
            return
        loader.load_next()


def test_thumbnail_cache_follows_the_workspace_switch(qt_app, tmp_path, monkeypatch):
    from screenkaffee.workspace import Workspace
    first, second = Workspace(tmp_path / "A"), Workspace(tmp_path / "B")
    second.add(Image.new("RGB", (50, 50), "blue"), "Privat B")
    window = main_window(first, monkeypatch)
    window.refresh()
    window.workspace = second                                                # wie in switch_workspace
    window.refresh()
    drain(window.thumbnail_loader)
    assert len(list((tmp_path / "B" / "thumbnails").glob("*.png"))) == 1
    assert not list((tmp_path / "A" / "thumbnails").glob("*.png"))
    window.close()
    first.close()
    second.close()


def test_missing_original_gets_a_placeholder_card(qt_app, tmp_path, monkeypatch):
    from screenkaffee.workspace import Workspace
    workspace = Workspace(tmp_path)
    ident = workspace.add(Image.new("RGB", (50, 50), "red"), "fehlt")
    workspace.path(ident).unlink()
    workspace.db.execute("UPDATE captures SET favorite=1 WHERE id=?", (ident,))
    workspace.db.commit()
    window = main_window(workspace, monkeypatch)
    window.refresh()
    drain(window.thumbnail_loader)
    assert not window.list.item(0).icon().isNull()
    window.close()
    workspace.close()


def test_thumbnail_loader_loads_many_cache_hits_per_tick_but_decodes_one(qt_app, tmp_path, monkeypatch):
    from screenkaffee.workspace import ThumbnailCache, Workspace
    workspace = Workspace(tmp_path)
    for index in range(12):
        workspace.add(Image.new("RGB", (20, 20), (index * 20, 0, 0)))
    window = main_window(workspace, monkeypatch)
    loader = window.thumbnail_loader
    monkeypatch.setattr(type(loader), "BUDGET", 5.0)                       # Zeitbudget im Test nie der Engpass
    decoded = []
    original_get = ThumbnailCache.get
    monkeypatch.setattr(ThumbnailCache, "get", lambda self, source: decoded.append(source) or original_get(self, source))
    ident = workspace.rows()[3]["id"]
    window.select_id(ident)
    window.refresh()
    loader.load_next()
    assert len(decoded) == 1 and len(loader.jobs) == 11                   # Fehlschlag: nur ein Original pro Takt
    drain(loader)
    assert len(decoded) == 12
    decoded.clear()
    window.refresh()
    loader.load_next()                                                      # jetzt alles im Cache: ein Takt reicht
    assert not loader.jobs and not decoded
    assert all(not window.list.item(i).icon().isNull() for i in range(window.list.count()))
    assert window.selected_ids() == [ident]
    window.close()
    workspace.close()


# --- G/L) Videoschnitt ---------------------------------------------------------------------------------------------

def make_clip(path, *extra):
    import subprocess
    from screenkaffee.media import ffmpeg_binary
    subprocess.run([ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=d=6:s=160x90:r=20",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", *extra, str(path)], check=True)
    return path


def stream_durations(path):
    import json
    from screenkaffee.integrations import run, tool
    ffprobe = tool("ffprobe")
    if not ffprobe:
        pytest.skip("ffprobe fehlt")
    result = run([ffprobe, "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(path)])
    return {s["codec_type"]: float(s["duration"]) for s in json.loads(result.stdout)["streams"]}


def test_tiny_rests_between_cuts_are_merged_away():
    from screenkaffee.media import kept_segments
    assert kept_segments(0, None, [(1, 2), (2.01, 3)], 6, min_length=0.05) == [(0, 1), (3, 6)]
    assert kept_segments(0, None, [(0.02, 2)], 6, min_length=0.05) == [(2, 6)]
    assert kept_segments(0, None, [(1, 2)], 6, min_length=0.05) == [(0, 1), (2, 6)]
    with pytest.raises(ValueError, match="weniger als ein Videobild"):
        kept_segments(0, None, [(0, 5.99)], 6, min_length=0.05)


def test_cut_export_keeps_picture_and_sound_in_step_and_explains_empty_results(tmp_path):
    from screenkaffee.media import edit_video
    source = make_clip(tmp_path / "quelle.mp4")
    result = edit_video(source, tmp_path / "geschnitten.mp4", cuts=[(1, 2), (2.01, 3)])
    durations = stream_durations(result)
    assert durations["video"] == pytest.approx(4.0, abs=0.03)
    assert durations["audio"] == pytest.approx(durations["video"], abs=0.03)
    with pytest.raises(ValueError, match="weniger als ein Videobild"):
        edit_video(source, tmp_path / "leer.mp4", cuts=[(0, 5.99)])


def test_trim_without_cuts_works_for_sources_with_start_offset(tmp_path):
    from screenkaffee.media import edit_video
    source = make_clip(tmp_path / "versatz.ts", "-f", "mpegts")
    result = edit_video(source, tmp_path / "teil.mp4", start=1, end=3)
    durations = stream_durations(result)
    assert durations["video"] == pytest.approx(2.0, abs=0.1)
    assert durations["audio"] == pytest.approx(2.0, abs=0.1)


# --- H) Kommandozeile ----------------------------------------------------------------------------------------------

def test_cli_video_reports_the_original_error_when_stop_fails_too(tmp_path, monkeypatch, capsys):
    import argparse
    import tempfile
    from screenkaffee import cli
    folder = tmp_path / "roh"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **kw: str(folder))
    monkeypatch.setattr(cli, "cli_policy", lambda *args: {})

    class Recorder:
        stopped = 0
        def start(self, folder, **kw):
            pass
        def error_if_stopped(self):
            return "ffmpeg: Gerät nicht gefunden"
        def stop(self):
            self.stopped += 1
            raise RuntimeError("Die Aufnahme enthält keine Videosegmente.")

    recorder = Recorder()
    args = argparse.Namespace(rect="0;0;100;100", delay=1, filepath=tmp_path / "aus.mp4")
    with pytest.raises(RuntimeError, match="Gerät nicht gefunden"):
        cli.run_video(args, recorder_factory=lambda: recorder, sleep=lambda s: None)
    assert recorder.stopped == 1
    error = capsys.readouterr().err
    assert "keine Videosegmente" in error and str(folder) in error


# --- I) Menü „Markierungen anordnen“ -------------------------------------------------------------------------------

def test_arrange_menu_does_not_pile_up_submenus(qt_app):
    from PySide6.QtWidgets import QMenu
    from screenkaffee.editor import EditorWindow
    window = EditorWindow(Image.new("RGB", (200, 100), "white"))
    before = len(window.arrange_menu.findChildren(QMenu))
    for _ in range(20):
        window.fill_arrange_menu()
    assert len(window.arrange_menu.findChildren(QMenu)) == before == 2
    titles = [action.text() for action in window.arrange_menu.actions()]
    assert "Anordnen (Ebenen)" in titles and "Ausrichten / Verteilen" in titles
    window.view.undo.setClean()
    window.close()


# --- J) F2-Dialog mit zu langem Sammlungsnamen ---------------------------------------------------------------------

def test_too_long_collection_name_in_details_dialog_shows_a_message(qt_app, tmp_path, monkeypatch):
    import sys
    from PySide6.QtWidgets import QDialog, QMessageBox
    from screenkaffee.workspace import Workspace
    workspace = Workspace(tmp_path)
    ident = workspace.add(Image.new("RGB", (20, 20)), "Bild")
    window = main_window(workspace, monkeypatch)
    window.select_id(ident)
    hooked, warnings = [], []
    monkeypatch.setattr(sys, "excepthook", lambda *args: hooked.append(args))
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: warnings.append(text))

    def fake_exec(dialog):
        dialog.collection_combo.setEditText("x" * 250)
        dialog.accept()                                                     # wie ein Klick auf OK
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    window.edit_details()
    assert not hooked
    assert warnings and "200 Zeichen" in warnings[0]
    assert workspace.get(ident)["collection"] == workspace.DEFAULT_COLLECTION
    window.close()
    workspace.close()


# --- K) Mehrfachauswahl ziehen -------------------------------------------------------------------------------------

def test_dragging_several_markings_is_one_undo_step_and_fits_with_arrow_keys(qt_app):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    from screenkaffee.editor import EditorView
    view = EditorView(Image.new("RGB", (500, 400), "white"))
    view.resize(700, 600)
    view.show()
    qt_app.processEvents()

    def rect(x, y):
        return view.add_annotation({"kind": "rectangle", "points": [0, 0, 60, 40], "pos": [x, y],
                                    "color": "#ff0000", "width": 6, "fill": "#ff0000"})

    a, b, c = rect(40, 40), rect(200, 40), rect(40, 200)
    start_a, start_b, start_c = a.pos(), b.pos(), c.pos()
    a.setSelected(True)
    b.setSelected(True)
    view.nudge_selected(5, 0)
    view.nudge_selected(5, 0)                                                # zusammengefasst: ein Schritt
    after_nudge = (a.pos(), b.pos())
    count = view.undo.count()
    grab = view.mapFromScene(a.mapToScene(QPointF(30, 20)))
    target = grab + QPointF(40, 30).toPoint()
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, grab)
    QTest.mouseMove(view.viewport(), grab + QPointF(10, 10).toPoint())
    QTest.mouseMove(view.viewport(), target)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target)
    qt_app.processEvents()
    moved = (a.pos() - after_nudge[0], b.pos() - after_nudge[1])
    assert moved[0] == moved[1] and moved[0] != QPointF(0, 0)                # beide mitgezogen
    assert view.undo.count() == count + 1 and c.pos() == start_c
    dragged = (a.pos(), b.pos())
    view.nudge_selected(0, 3)                                                 # Pfeiltaste danach: eigener Schritt
    assert view.undo.count() == count + 2
    view.undo.undo()
    assert (a.pos(), b.pos()) == dragged
    view.undo.undo()                                                           # Ziehen: beide zurück
    assert (a.pos(), b.pos()) == after_nudge
    view.undo.undo()                                                           # Pfeiltasten: beide zurück
    assert (a.pos(), b.pos()) == (start_a, start_b)
    view.undo.redo()
    view.undo.redo()
    assert (a.pos(), b.pos()) == dragged
    view.undo.clear()
    view.close()
