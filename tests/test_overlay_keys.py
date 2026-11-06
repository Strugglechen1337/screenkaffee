from PIL import Image
from PySide6.QtCore import QPointF, Qt, QRect
from PySide6.QtTest import QTest
from screenkaffee.overlay import SelectionOverlay


def make_overlay(qt_app, size=(400, 300), shape=(200, 150)):
    image = Image.new("RGB", size, "white")
    overlay = SelectionOverlay(image, QRect(0, 0, *shape))
    overlay.show()
    qt_app.processEvents()
    return overlay


def test_help_and_cursor_keys(qt_app):
    overlay = make_overlay(qt_app)
    toggled = []
    overlay.cursorToggled.connect(toggled.append)
    assert overlay.show_help and not overlay.show_cursor
    QTest.keyClick(overlay, Qt.Key.Key_H)
    assert not overlay.show_help
    QTest.keyClick(overlay, Qt.Key.Key_H)
    assert overlay.show_help
    QTest.keyClick(overlay, Qt.Key.Key_M)
    QTest.keyClick(overlay, Qt.Key.Key_M)
    assert toggled == [True, False]
    overlay.cancel()


def test_wheel_zoom_keeps_the_pixel_under_the_cursor_and_is_limited(qt_app):
    overlay = make_overlay(qt_app)
    anchor = QPointF(100, 75)
    before = overlay.pixel(anchor)
    overlay.set_zoom(4, anchor)
    after = overlay.pixel(anchor)
    assert (round(before.x()), round(before.y())) == (round(after.x()), round(after.y())) == (200, 150)
    overlay.set_zoom(100, anchor)
    assert overlay.zoom == overlay.MAX_ZOOM
    overlay.set_zoom(0.1, anchor)
    assert overlay.zoom == 1.0 and overlay.origin == QPointF(0, 0)
    overlay.cancel()


def test_selection_while_zoomed_maps_to_the_right_pixels(qt_app):
    overlay = make_overlay(qt_app)
    overlay.set_zoom(2, QPointF(0, 0))                       # shows the top-left quarter of the overlay
    boxes = []
    overlay.regionSelected.connect(boxes.append)
    QTest.mousePress(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, overlay.rect().topLeft() + QRect(20, 10, 0, 0).topLeft())
    QTest.mouseMove(overlay, overlay.rect().topLeft() + QRect(60, 50, 0, 0).topLeft())
    QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, overlay.rect().topLeft() + QRect(60, 50, 0, 0).topLeft())
    # view (20,10)-(60,50) at zoom 2 = overlay (10,5)-(30,25) = image pixels (20,10)-(60,50) (image is 2x the overlay);
    # like before, the release point itself belongs to the selection, hence one extra pixel at the far edge.
    assert boxes == [(20, 10, 61, 51)]


def test_zoom_is_ignored_during_a_selection_and_unzoomed_selection_is_unchanged(qt_app):
    overlay = make_overlay(qt_app)
    boxes = []
    overlay.regionSelected.connect(boxes.append)
    QTest.mousePress(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QRect(10, 10, 0, 0).topLeft())
    overlay.start = QRect(10, 10, 0, 0).topLeft()
    class Wheel:
        def angleDelta(self):
            from PySide6.QtCore import QPoint
            return QPoint(0, 120)
        def position(self):
            return QPointF(50, 50)
        def accept(self):
            pass
    overlay.wheelEvent(Wheel())
    assert overlay.zoom == 1.0
    QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QRect(110, 60, 0, 0).topLeft())
    assert boxes == [(20, 20, 222, 122)]


def test_m_toggles_cursor_live_on_freeze_frame_and_arrows_nudge(qt_app):
    from PySide6.QtCore import QPoint
    overlay = SelectionOverlay(Image.new("RGB", (200, 150), "#336699"), QRect(0, 0, 200, 150))
    overlay.show()
    qt_app.processEvents()
    overlay.end = QPoint(60, 50)
    before = overlay.pixmap.toImage().pixelColor(62, 54).rgb()
    QTest.keyClick(overlay, Qt.Key.Key_M)
    with_cursor = overlay.pixmap.toImage().pixelColor(62, 54).rgb()
    assert overlay.show_cursor is True and with_cursor != before
    QTest.keyClick(overlay, Qt.Key.Key_M)
    after = overlay.pixmap.toImage().pixelColor(62, 54).rgb()
    assert overlay.show_cursor is False and after == before
    QTest.keyClick(overlay, Qt.Key.Key_Right)
    QTest.keyClick(overlay, Qt.Key.Key_Down)
    assert overlay.end == QPoint(61, 51)
    overlay.cancel()


def test_overlay_starts_with_drawn_cursor_when_show_cursor_is_true_and_m_removes_it(qt_app):
    from PySide6.QtCore import QPoint
    base = Image.new("RGB", (200, 150), "#336699")
    overlay = SelectionOverlay(base, QRect(0, 0, 200, 150), show_cursor=True, cursor_pos=(60, 50))
    overlay.show()
    qt_app.processEvents()
    clean_rgb = SelectionOverlay(base, QRect(0, 0, 200, 150), show_cursor=False).pixmap.toImage().pixelColor(62, 54).rgb()
    assert overlay.pixmap.toImage().pixelColor(62, 54).rgb() != clean_rgb
    QTest.keyClick(overlay, Qt.Key.Key_M)
    assert overlay.show_cursor is False
    assert overlay.pixmap.toImage().pixelColor(62, 54).rgb() == clean_rgb
    overlay.cancel()


def test_region_mode_populates_window_snap_and_hyprland_clients(qt_app, tmp_path, monkeypatch):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    from screenkaffee import integrations
    import json, subprocess
    def fake_run(args, **kw):
        if "monitors" in args:
            out = json.dumps([{"activeWorkspace": {"id": 1}}]).encode()
        else:
            out = json.dumps([
                {"title": "Editor", "at": [20, 30], "size": [120, 80], "workspace": {"id": 1}, "mapped": True, "hidden": False},
                {"title": "Andere", "at": [10, 10], "size": [100, 80], "workspace": {"id": 2}, "mapped": True, "hidden": False},
            ]).encode()
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr=b"")
    monkeypatch.setattr(integrations, "tool", lambda name: "/usr/bin/hyprctl" if name == "hyprctl" else None)
    monkeypatch.setattr(integrations, "run", fake_run)
    assert integrations.hyprland_window_rectangles() == [("Editor", (20, 30, 140, 110))]
    window = MainWindow(Workspace(tmp_path))
    if window.hotkeys:
        window.hotkeys.close()
        qt_app.removeNativeEventFilter(window.hotkeys)
    import screenkaffee.windows_capture as wc
    monkeypatch.setattr(wc, "window_rectangles", lambda include_handles=False: [("Testfenster", (10, 15, 110, 95), 12345)])
    window.show_overlay(Image.new("RGB", (200, 150), "white"), QRect(0, 0, 200, 150), {"desktop_bounds": [0, 0, 200, 150]}, kind="region")
    assert len(window.overlays) == 1
    ov = window.overlays[0]
    assert len(ov.windows) == 1 and ov.windows[0][0] == "Testfenster" and ov.window_handles == [12345]
    ov.cancel()
    window._capture_cursor_pos = QRect(40, 35, 1, 1).topLeft()
    window.show_overlay(Image.new("RGB", (400, 300), "white"), QRect(0, 0, 200, 150), {"desktop_bounds": [0, 0, 400, 300]}, kind="region")
    ov2 = window.overlays[-1]
    assert ov2.cursor_pos == (80, 70)
    ov2.cancel()
    window.tray.hide()
    window.workspace.close()
    window.deleteLater()


def test_escape_cancels_overlay_idle_while_dragging_and_in_precise_mode(qt_app):
    from PySide6.QtCore import QPoint
    # 1. Escape immediately when overlay opens (idle)
    ov1 = make_overlay(qt_app)
    assert ov1.focusPolicy() == Qt.FocusPolicy.StrongFocus
    cancelled1, selected1 = [], []
    ov1.cancelled.connect(lambda: cancelled1.append(True))
    ov1.selected.connect(selected1.append)
    QTest.keyClick(ov1, Qt.Key.Key_Escape)
    assert cancelled1 == [True] and selected1 == [] and ov1.done

    # 2. Escape while dragging a selection rectangle (mouseRelease afterwards must be ignored)
    ov2 = make_overlay(qt_app)
    cancelled2, selected2, boxes2 = [], [], []
    ov2.cancelled.connect(lambda: cancelled2.append(True))
    ov2.selected.connect(selected2.append)
    ov2.regionSelected.connect(boxes2.append)
    QTest.mousePress(ov2, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(20, 20))
    QTest.mouseMove(ov2, QPoint(120, 90))
    assert ov2.start is not None
    QTest.keyClick(ov2, Qt.Key.Key_Escape)
    assert ov2.done and ov2.start is None and cancelled2 == [True]
    QTest.mouseRelease(ov2, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(120, 90))
    assert selected2 == [] and boxes2 == [] and len(cancelled2) == 1

    # 3. Escape during Shift precise_box mode
    ov3 = make_overlay(qt_app)
    cancelled3, selected3 = [], []
    ov3.cancelled.connect(lambda: cancelled3.append(True))
    ov3.selected.connect(selected3.append)
    QTest.mousePress(ov3, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, QPoint(20, 20))
    QTest.mouseMove(ov3, QPoint(100, 80))
    QTest.mouseRelease(ov3, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, QPoint(100, 80))
    assert ov3.precise_box is not None and not ov3.done
    QTest.keyClick(ov3, Qt.Key.Key_Escape)
    assert ov3.done and ov3.precise_box is None and cancelled3 == [True] and selected3 == []


def test_escape_cancels_pending_delayed_capture_and_region_adjuster(qt_app, tmp_path):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    from screenkaffee.regionadjust import RegionAdjuster
    window = MainWindow(Workspace(tmp_path))
    if window.hotkeys:
        window.hotkeys.close()
        qt_app.removeNativeEventFilter(window.hotkeys)
    # 1. Start a delayed capture and cancel with Escape before timer fires
    window.delay.setValue(5)
    window.capture("region")
    token_before = window._capture_token
    assert window.capture_pending
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert not window.capture_pending and window._capture_token > token_before
    assert "abgebrochen" in window.statusBar().currentMessage()
    window.delay.setValue(0)

    # 2. RegionAdjuster Escape and right-click cancel
    adj = RegionAdjuster([40, 40, 200, 120], scale=1.0, geometry=QRect(0, 0, 400, 300))
    adj.show()
    qt_app.processEvents()
    assert adj.focusPolicy() == Qt.FocusPolicy.StrongFocus
    adj_cancelled = []
    adj.cancelled.connect(lambda: adj_cancelled.append(True))
    QTest.keyClick(adj, Qt.Key.Key_Escape)
    assert adj.finished and adj_cancelled == [True]

    window.tray.hide()
    window.workspace.close()
    window.deleteLater()
