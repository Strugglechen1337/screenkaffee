"""Schritt-Anleitung, Befehlssuche, Live-Zeichnen, Lineal, Markierungs-Vorlagen und Aufräum-Helfer."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image, ImageDraw


def window_for(tmp_path, monkeypatch):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    monkeypatch.setattr(MainWindow, "apply_hotkeys", lambda self: {})
    return MainWindow(Workspace(tmp_path))


# --- Schritt-Anleitung ---------------------------------------------------------------------
def test_mark_click_draws_ring_and_number_without_touching_original():
    from screenkaffee.steps import mark_click
    image = Image.new("RGB", (400, 300), "white")
    marked = mark_click(image, 200, 150, 3)
    assert image.getpixel((200, 128)) == (255, 255, 255)
    assert marked.getpixel((200, 128))[0] > 200 and marked.getpixel((200, 128))[1] < 100       # Ring oben
    assert marked.size == image.size
    corner = mark_click(image, 398, 2, 12)                                                       # am Rand: kein Fehler
    assert corner.size == image.size


def test_step_recorder_counts_presses_not_holds_and_ignores_double_clicks():
    from screenkaffee.steps import StepRecorder
    recorder = StepRecorder()
    events = [(False, 0), (True, .1), (True, .2), (False, .3), (True, .4), (False, .45), (True, 1.0), (False, 1.1)]
    due = [now for pressed, now in events if recorder.feed(pressed, now)]
    assert due == [.1, 1.0]                     # .4 ist zu nah am ersten Klick (Doppelklick)


def test_step_session_takes_shots_skips_own_windows_and_finishes(qt_app):
    from screenkaffee.steps import StepSession
    state = {"down": False, "own": False, "now": 0.0}
    finished = []
    session = StepSession(finished.append, button_state=lambda: state["down"],
                          grab=lambda: (Image.new("RGB", (200, 100), "white"), 50, 40, 1.0),
                          own_window=lambda: state["own"], clock=lambda: state["now"])
    session.start()
    for down, own, now in ((True, False, 1), (False, False, 1.1), (True, True, 2), (False, False, 2.1), (True, False, 3)):
        state.update(down=down, own=own, now=now)
        session.tick()
    assert len(session.recorder.steps) == 2 and "2 Schritte" in session.bar.label.text()
    session.finish()
    assert len(finished[0]) == 2 and session.bar is None and not session.timer.isActive()


def test_step_guide_finished_stores_collection(qt_app, tmp_path, monkeypatch):
    from screenkaffee.steps import Step, mark_click
    window = window_for(tmp_path, monkeypatch)
    steps = [Step(mark_click(Image.new("RGB", (120, 80), "white"), 30, 30, n), (30, 30), "Explorer") for n in (1, 2, 3)]
    ids = window.step_guide_finished(steps, open_designer=False)
    rows = [window.workspace.get(ident) for ident in ids]
    assert [row["title"] for row in rows] == ["Schritt 1", "Schritt 2", "Schritt 3"]
    assert rows[0]["collection"].startswith("Anleitung ") and json.loads(rows[0]["metadata"])["window"] == "Explorer"
    assert window.step_session is None and window.step_guide_finished([], open_designer=False) == []


def test_describe_mentions_window():
    from screenkaffee.steps import Step, describe
    assert describe(Step(None, (1, 2), "Einstellungen"), 4) == "Schritt 4: An der markierten Stelle klicken in „Einstellungen“."


# --- Befehlssuche -----------------------------------------------------------------------------
def test_palette_search_folds_umlauts_and_ranks_word_starts():
    from screenkaffee.palette import fold, score
    assert fold("Größe …") == "groesse "
    assert score("groesse", "Größe ändern") is not None and score("xyz", "Größe") is None
    assert score("zeit", "Video › Zeitraffer starten") < score("zeit", "Hilfe › Arbeitszeitkonto")


def test_command_palette_finds_menu_actions_and_runs_them(qt_app, tmp_path, monkeypatch):
    from screenkaffee.palette import CommandPalette, search
    window = window_for(tmp_path, monkeypatch)
    entries = window.command_entries()
    labels = [label for label, _ in entries]
    assert "Video › Zeitraffer starten …" in labels and "Bereich aufnehmen" in labels
    assert "Hilfe und Updates › Nach Updates suchen …" in labels
    assert search("updates", entries)[0][0] == "Hilfe und Updates › Nach Updates suchen …"
    assert search("zeitraf", entries)[0][0] == "Video › Zeitraffer starten …"
    dialog = CommandPalette(entries, window)
    dialog.field.setText("vergleich")
    assert dialog.listing.currentItem().text() == "Auswahl bearbeiten › Zwei Aufnahmen vergleichen …"
    called = []
    monkeypatch.setattr(window, "toggle_timelapse", lambda: called.append("lapse"))
    dialog.field.setText("zeitraffer")
    dialog.run_current()
    dialog.chosen.trigger()
    assert called == ["lapse"]


# --- Live-Zeichnen ---------------------------------------------------------------------------
def test_live_draw_strokes_fade_and_keys(qt_app):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QKeyEvent, QMouseEvent
    from screenkaffee.livedraw import LiveDrawSession, Stroke, opacity
    stroke = Stroke("arrow", "#ff0000", 4, [(0, 0), (10, 10)], finished=10)
    assert opacity(stroke, 12, 3, 1) == 1 and opacity(stroke, 13.5, 3, 1) == pytest.approx(.5) and opacity(stroke, 20, 3, 1) == 0
    assert opacity(stroke, 99, 0, 1) == 1                                      # 0 = bleibt stehen
    clock = {"now": 0.0}
    ended = []
    session = LiveDrawSession(hold=2, on_end=lambda: ended.append(True), clock=lambda: clock["now"])
    window = session.windows[0]

    def mouse(kind, x, y, button=Qt.MouseButton.LeftButton):
        buttons = button if kind != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton
        event = QMouseEvent(kind, QPointF(x, y), QPointF(x, y), button, buttons, Qt.KeyboardModifier.NoModifier)
        {QEvent.Type.MouseButtonPress: window.mousePressEvent, QEvent.Type.MouseMove: window.mouseMoveEvent,
         QEvent.Type.MouseButtonRelease: window.mouseReleaseEvent}[kind](event)

    def key(k, text=""):
        session.key(QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier, text))
    key(Qt.Key.Key_P, "p")
    key(Qt.Key.Key_3, "3")
    mouse(QEvent.Type.MouseButtonPress, 10, 10)
    mouse(QEvent.Type.MouseMove, 20, 25)
    mouse(QEvent.Type.MouseMove, 40, 30)
    mouse(QEvent.Type.MouseButtonRelease, 40, 30)
    assert window.strokes[0].tool == "pen" and window.strokes[0].color == "#22c55e" and len(window.strokes[0].points) == 3
    window.grab()                                                               # zeichnet ohne Fehler
    clock["now"] = 10
    window.tick()
    assert window.strokes == []                                                 # verblasst
    key(Qt.Key.Key_H, "h")
    assert session.state.hold == 0
    mouse(QEvent.Type.MouseButtonPress, 5, 5)
    mouse(QEvent.Type.MouseButtonRelease, 50, 50)
    clock["now"] = 100
    window.tick()
    assert len(window.strokes) == 1
    key(Qt.Key.Key_Backspace)
    assert window.strokes == []
    key(Qt.Key.Key_Escape)
    assert ended == [True] and not session.active


def test_live_draw_toggle_and_hotkey(qt_app, tmp_path, monkeypatch):
    from screenkaffee.hotkeys import ACTIONS, DEFAULT_HOTKEYS
    assert DEFAULT_HOTKEYS["draw"] == "Ctrl+Alt+D" and {"steps", "draw", "measure"} <= dict(ACTIONS).keys()
    window = window_for(tmp_path, monkeypatch)
    window.command_action("draw")
    assert window.live_draw is not None and window.live_draw_action.text() == "Live-Zeichnen beenden"
    window.live_draw.state.hold = 0
    window.command_action("draw")
    assert window.live_draw is None and window.settings["live_draw_hold"] == 0


# --- Lineal ----------------------------------------------------------------------------------------
def test_ruler_measurement_and_snap():
    from screenkaffee.ruler import clipboard_text, label, measurement, snap_axis
    values = measurement(10, 10, 40, 50)
    assert values["length"] == 50 and values["dx"] == 30 and values["dy"] == 40
    assert measurement(0, 0, 10, 0)["angle"] == 0 and measurement(0, 0, 0, -10)["angle"] == 90
    assert snap_axis(0, 0, 100, 7) == (100, 0) and snap_axis(0, 0, 4, 90) == (0, 90) and snap_axis(0, 0, 50, 40) == (50, 50)
    assert label(values).startswith("50 px") and "53,1°" in label(values)
    assert clipboard_text(values) == "30 × 40 px (Länge 50 px)"


def test_overlay_ruler_mode_measures_in_image_pixels(qt_app):
    from PySide6.QtCore import QEvent, QPointF, QRect, Qt
    from PySide6.QtGui import QKeyEvent, QMouseEvent
    from PySide6.QtWidgets import QApplication
    from screenkaffee.overlay import SelectionOverlay
    overlay = SelectionOverlay(Image.new("RGB", (800, 600), "white"), QRect(0, 0, 400, 300))
    selected = []
    overlay.selected.connect(selected.append)
    overlay.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_L, Qt.KeyboardModifier.NoModifier, "l"))
    assert overlay.measure_mode

    def mouse(kind, x, y, modifiers=Qt.KeyboardModifier.NoModifier):
        buttons = Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton
        return QMouseEvent(kind, QPointF(x, y), QPointF(x, y), Qt.MouseButton.LeftButton, buttons, modifiers)
    overlay.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, 10, 10))
    overlay.mouseMoveEvent(mouse(QEvent.Type.MouseMove, 100, 14, Qt.KeyboardModifier.ShiftModifier))
    overlay.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, 100, 14, Qt.KeyboardModifier.ShiftModifier))
    values = overlay.measurement()
    assert values["dx"] == 180 and values["dy"] == 0                           # doppelte Auflösung, waagerecht eingerastet
    assert selected == [] and not overlay.done                                  # Messen nimmt nichts auf
    overlay.grab()
    overlay.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier, "c"))
    assert QApplication.clipboard().text() == "180 × 0 px (Länge 180 px)"
    overlay.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_L, Qt.KeyboardModifier.NoModifier, "l"))
    assert not overlay.measure_mode and overlay.measure_line is None
    overlay.close()


def test_editor_measure_tool_is_not_saved(qt_app):
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from screenkaffee.editor import EditorWindow
    editor = EditorWindow(Image.new("RGB", (300, 200), "white"), "x")
    editor.resize(900, 700)
    editor.show()
    editor.select_tool("measure")
    view = editor.view
    assert view.tool == "measure" and editor.palette_actions["measure"].isChecked()
    a, b = view.mapFromScene(QPointF(20, 30)), view.mapFromScene(QPointF(80, 110))

    def mouse(kind, point):
        buttons = Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton
        return QMouseEvent(kind, QPointF(point), QPointF(point), Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
    messages = []
    view.measured.connect(messages.append)
    view.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, a))
    view.mouseMoveEvent(mouse(QEvent.Type.MouseMove, b))
    view.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, b))
    values = view.measurement()
    assert values["length"] == pytest.approx(100, abs=2) and messages and messages[0].startswith(str(round(values["length"])))
    view.viewport().grab()
    assert view.annotations() == []
    assert view.render_image().getpixel((50, 70)) == (255, 255, 255)             # nichts im Ergebnis
    editor.select_tool("select")
    assert view.measure_line is None
    editor.close()


# --- Vorlagen ----------------------------------------------------------------------------------------
def test_template_geometry_helpers():
    from screenkaffee.templates import bounds, normalize, placed
    items = [{"kind": "rectangle", "points": [10, 20, 60, 80], "pos": [5, 5]},
             {"kind": "text", "points": [0, 0, 10, 10], "text_width": 100, "text_height": 30, "pos": [100, 100]}]
    assert bounds(items) == (15, 25, 200, 130)
    assert bounds(normalize(items))[:2] == (0, 0)
    left, top, right, bottom = bounds(placed(items, (500, 400)))
    assert ((left + right) / 2, (top + bottom) / 2) == (500, 400)
    assert items[0]["pos"] == [5, 5]                                            # Original unverändert


def test_template_library_roundtrip(tmp_path):
    from screenkaffee.templates import TemplateLibrary, builtin_templates
    library = TemplateLibrary(tmp_path)
    assert len(library.entries()) == len(builtin_templates()) >= 4
    with pytest.raises(ValueError):
        library.add("Hier klicken", [{"kind": "arrow", "points": [0, 0, 1, 1]}])
    with pytest.raises(ValueError):
        library.add("Leer", [])
    library.add("  Mein   Logo ", [{"kind": "arrow", "points": [10, 10, 50, 50], "pos": [100, 100], "rotation": 0}])
    own = library.own()
    assert own[0]["name"] == "Mein Logo" and own[0]["items"][0]["pos"] == [-10, -10] and "rotation" not in own[0]["items"][0]
    with pytest.raises(ValueError):
        library.remove("Hier klicken")
    library.remove("Mein Logo")
    assert library.own() == []


def test_editor_inserts_and_saves_templates(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("SCREENKAFFEE_DATA", str(tmp_path))
    from screenkaffee.editor import EditorWindow
    editor = EditorWindow(Image.new("RGB", (800, 600), "white"), "x")
    labels = [a.text() for a in editor.templates_menu.actions() if not a.isSeparator()]
    assert labels[:2] == ["Hier klicken", "Wichtig (Rahmen)"] and "Auswahl als Vorlage speichern …" in labels
    entry = next(e for e in __import__("screenkaffee.templates", fromlist=["x"]).builtin_templates() if e["name"] == "Hier klicken")
    items = editor.insert_template(entry)
    assert len(editor.view.annotations()) == 2 and all(item.isSelected() for item in items)
    editor.view.undo.undo()
    assert editor.view.annotations() == []                                      # ein Schritt rückgängig
    editor.view.undo.redo()
    assert editor.save_template("Pfeil mit Text") == "Pfeil mit Text"
    editor.fill_templates_menu()
    labels = [a.text() for a in editor.templates_menu.actions() if not a.isSeparator()]
    assert "Pfeil mit Text" in labels and "Eigene Vorlage löschen" in labels
    editor.discard_changes = True
    editor.close()


# --- Aufräumen ----------------------------------------------------------------------------------------
def screenshot(seed, noise=0):
    image = Image.new("RGB", (320, 200), "white")
    draw = ImageDraw.Draw(image)
    for index in range(6):
        x = (seed * 37 + index * 53) % 280
        draw.rectangle((x, index * 30, x + 40, index * 30 + 20), fill=(seed * 40 % 255, 80, 200))
    if noise:
        draw.point((5, 5), fill="black")
    return image


def test_dhash_finds_duplicates_but_not_different_images():
    from screenkaffee.cleanup import dhash, distance
    assert distance(dhash(screenshot(1)), dhash(screenshot(1, noise=1))) <= 2
    assert distance(dhash(screenshot(1)), dhash(screenshot(4))) > 10


def test_scan_groups_duplicates_lists_big_old_videos_and_empties_trash(qt_app, tmp_path, monkeypatch):
    from screenkaffee import cleanup
    from screenkaffee.workspace import Workspace
    workspace = Workspace(tmp_path)
    first = workspace.add(screenshot(1), title="A")
    copy = workspace.add(screenshot(1), title="A Kopie")
    other = workspace.add(screenshot(4), title="B")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\0" * 2000)
    clip = workspace.add_file(video, title="Altes Video")
    workspace.db.execute("UPDATE captures SET created=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(days=200)).isoformat(), clip))
    workspace.db.commit()
    monkeypatch.setattr(cleanup, "OLD_VIDEO", 1000)
    report = cleanup.scan(workspace)
    assert report.scanned == 4 and len(report.groups) == 1
    assert {e.ident for e in report.groups[0]} == {first, copy} and other not in report.suggested()
    assert len(report.suggested()) == 1 and report.reclaimable() > 0
    assert [(e.ident, reason) for e, reason in report.videos] == [(clip, "älter als 90 Tage")]
    dialog = cleanup.cleanup_dialog(None, workspace, report)
    assert dialog.checked() == report.suggested()
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.question", lambda *a, **k: __import__("PySide6.QtWidgets", fromlist=["x"]).QMessageBox.StandardButton.Yes)
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *a, **k: None)
    removed = report.suggested()
    dialog.remove_checked()
    assert workspace.get(removed[0]) is None and len(workspace.trash_entries()) == 1
    assert report.trash_bytes > 0
    dialog.empty_trash()
    assert workspace.trash_entries() == [] and not any((tmp_path / "trash").iterdir())


def test_menus_offer_new_features(qt_app, tmp_path, monkeypatch):
    window = window_for(tmp_path, monkeypatch)
    labels = [label for label, _ in window.command_entries()]
    for text in ("Aufnehmen › Schritt-Anleitung aufnehmen …", "Aufnehmen › Bildschirm ausmessen (Lineal)",
                 "Video › Live-Zeichnen auf dem Bildschirm", "Sammlungen und Daten › Aufräumen (doppelte Bilder, große Videos) …",
                 "Hilfe und Updates › Befehl suchen … (Strg+K)"):
        assert text in labels, text
    assert window.live_draw_action in window.video_more_button.menu().actions()
