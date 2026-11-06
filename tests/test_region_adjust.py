import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from screenkaffee.regionadjust import RegionAdjuster, adjust_rect, centered, handle_points, hit_test

BOUNDS = (0, 0, 1920, 1080)


def test_hit_test_finds_handles_edges_and_the_inside():
    rect = (100, 100, 400, 300)
    assert hit_test(rect, (100, 100)) == "nw" and hit_test(rect, (500, 400)) == "se"
    assert hit_test(rect, (300, 100)) == "n" and hit_test(rect, (500, 250)) == "e"
    assert hit_test(rect, (300, 250)) == "move"
    assert hit_test(rect, (50, 50)) is None and hit_test(rect, (700, 700)) is None
    assert set(handle_points(rect)) == {"nw", "n", "ne", "e", "se", "s", "sw", "w"}


def test_dragging_edges_and_corners_resizes_from_the_right_side():
    rect = [100, 100, 400, 300]
    assert adjust_rect(rect, "e", 51, 99, BOUNDS) == [100, 100, 450, 300]        # width stays even, y ignored
    assert adjust_rect(rect, "w", -50, 0, BOUNDS) == [50, 100, 450, 300]
    assert adjust_rect(rect, "s", 0, 100, BOUNDS) == [100, 100, 400, 400]
    assert adjust_rect(rect, "n", 0, -40, BOUNDS) == [100, 60, 400, 340]
    assert adjust_rect(rect, "se", 20, 30, BOUNDS) == [100, 100, 420, 330]
    assert adjust_rect(rect, "nw", 20, 30, BOUNDS) == [120, 130, 380, 270]


def test_moving_and_resizing_never_leave_the_bounds_or_get_tiny():
    rect = [100, 100, 400, 300]
    assert adjust_rect(rect, "move", -500, -500, BOUNDS) == [0, 0, 400, 300]
    assert adjust_rect(rect, "move", 5000, 5000, BOUNDS) == [1520, 780, 400, 300]
    assert adjust_rect(rect, "e", -5000, 0, BOUNDS) == [100, 100, 64, 300]
    assert adjust_rect(rect, "w", 5000, 0, BOUNDS)[2] == 64
    assert adjust_rect(rect, "se", 5000, 5000, BOUNDS) == [100, 100, 1820, 980]
    assert adjust_rect(rect, "nw", -5000, -5000, BOUNDS) == [0, 0, 500, 400]
    assert all(v % 2 == 0 for v in adjust_rect([101, 101, 401, 301], "se", 3, 3, BOUNDS)[2:])


def test_standard_sizes_are_centred_and_fit_the_bounds():
    assert centered([100, 100, 400, 300], (640, 360), BOUNDS) == [0, 70, 640, 360]         # x = -20 is pushed back to 0
    assert centered([760, 440, 400, 200], (1280, 720), BOUNDS) == [320, 180, 1280, 720]
    assert centered([0, 0, 100, 100], (1280, 720), BOUNDS) == [0, 0, 1280, 720]            # pushed back inside
    assert centered([0, 0, 100, 100], (4000, 3000), (0, 0, 800, 600)) == [0, 0, 800, 600]


def make(qt_app, region=(100, 80, 200, 120), geometry=QRect(0, 0, 800, 600), scale=1.0):
    widget = RegionAdjuster(list(region), scale, geometry)
    widget.show()
    qt_app.processEvents()
    return widget


def test_dragging_the_handles_with_the_mouse_changes_the_region(qt_app):
    widget = make(qt_app)
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(300, 200))        # se corner
    QTest.mouseMove(widget, QPoint(380, 260))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(380, 260))
    assert widget.region == [100, 80, 280, 180] and widget.size_label.text() == "280 × 180 px"
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(200, 150))        # inside: move
    QTest.mouseMove(widget, QPoint(250, 190))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(250, 190))
    assert widget.region == [150, 120, 280, 180]
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(10, 10))          # outside: nothing
    QTest.mouseMove(widget, QPoint(60, 60))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(60, 60))
    assert widget.region == [150, 120, 280, 180]
    widget.cancel()


def test_scale_converts_between_native_pixels_and_screen_units(qt_app):
    widget = make(qt_app, region=(200, 160, 400, 240), geometry=QRect(0, 0, 800, 600), scale=2.0)
    assert widget.bounds == [0, 0, 1600, 1200]
    assert widget.view_rect().getRect() == (100.0, 80.0, 200.0, 120.0)
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(300, 200))
    QTest.mouseMove(widget, QPoint(310, 210))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(310, 210))
    assert widget.region == [200, 160, 420, 260]            # 10 screen units = 20 native pixels
    widget.cancel()


def test_size_menu_start_and_cancel(qt_app):
    widget = make(qt_app, region=(200, 150, 300, 200), geometry=QRect(0, 0, 1920, 1080))
    started, cancelled = [], []
    widget.started.connect(started.append)
    widget.cancelled.connect(lambda: cancelled.append(True))
    index = widget.size_box.findText("1280 × 720")
    widget.size_box.setCurrentIndex(index)
    widget.size_chosen(index)
    assert widget.region == [0, 0, 1280, 720]
    widget.start_button.click()
    assert started == [widget.region] and not cancelled
    second = make(qt_app)
    second.cancelled.connect(lambda: cancelled.append(True))
    QTest.keyClick(second, Qt.Key.Key_Escape)
    assert cancelled == [True]
    third = make(qt_app)
    got = []
    third.started.connect(got.append)
    QTest.keyClick(third, Qt.Key.Key_Return)
    assert got == [[100, 80, 200, 120]]


def test_the_controls_stay_on_screen_even_at_the_bottom_edge(qt_app):
    widget = make(qt_app, region=(100, 480, 400, 100))
    assert widget.start_button.y() < widget.view_rect().top()               # no room below: the buttons go above
    widget.cancel()
    low = make(qt_app, region=(100, 100, 400, 100))
    assert low.start_button.y() > low.view_rect().bottom()
    low.cancel()


def test_main_window_shows_the_adjuster_and_starts_with_the_chosen_region(qt_app, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    window = MainWindow(Workspace(tmp_path))
    began = []
    monkeypatch.setattr(window, "begin_video_recording", began.append)
    monkeypatch.setattr(QTimer, "singleShot", staticmethod(lambda ms, callback: callback()))
    window.adjust_video_region([100, 100, 320, 200])
    window.region_adjuster.started.emit([120, 140, 640, 360])
    assert began == [[120, 140, 640, 360]] and window.settings["last_region"] == [120, 140, 640, 360]
    window.region_adjuster.close()
    window.adjust_video_region(None)                        # no remembered region: start directly (whole desktop)
    assert began[-1] is None
