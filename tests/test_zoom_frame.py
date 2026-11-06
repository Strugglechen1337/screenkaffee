import pytest
from PySide6.QtWidgets import QApplication
from screenkaffee.capture_options import pick_screen
from screenkaffee.videoextras import ZoomFrame, zoom_frame_rect


def test_zoom_frame_matches_the_zoom_filter_maths():
    region = (0, 0, 1000, 500)
    assert zoom_frame_rect(region, 2, .5, .5) == [250, 125, 500, 250]
    assert zoom_frame_rect(region, 2, 0, 0) == [0, 0, 500, 250]              # clamped at the top-left
    assert zoom_frame_rect(region, 2, 1, 1) == [500, 250, 500, 250]          # clamped at the bottom-right
    assert zoom_frame_rect(region, 1, .3, .7) == [0, 0, 1000, 500]
    assert zoom_frame_rect((100, 200, 1000, 500), 4, .5, .5) == [475, 387.5, 250, 125]
    assert zoom_frame_rect(region, 8, .5, .5)[2:] == [125, 62.5]


def test_zoom_frame_widget_is_a_click_through_green_frame(qt_app):
    from PySide6.QtCore import Qt
    frame = ZoomFrame()
    assert frame.windowFlags() & Qt.WindowType.WindowTransparentForInput
    frame.show_area([200, 100, 400, 300], scale=2)
    assert frame.isVisible() and (frame.x(), frame.y(), frame.width(), frame.height()) == (100, 50, 200, 150)
    picture = frame.grab().toImage()
    assert picture.pixelColor(2, 75).name() == "#22c55e" and picture.pixelColor(100, 75).alpha() == 0
    frame.hide()


class FakeRecorder:
    active = True

    def __init__(self):
        self.options = {"region": [0, 0, 1000, 500]}
        self.zooms = []

    def set_zoom(self, factor, x, y):
        self.zooms.append((factor, x, y))


def test_zoom_keys_show_and_hide_the_frame_while_recording(qt_app, tmp_path):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    window = MainWindow(Workspace(tmp_path))
    window.recorder = FakeRecorder()
    window.video_zoom_factor = 1
    window.change_video_zoom(1)
    assert window.video_zoom_factor == 1.5 and window.recorder.zooms[0][0] == 1.5
    scale = QApplication.primaryScreen().devicePixelRatio()
    assert window.zoom_frame.isVisible() and window.zoom_frame.width() == round(1000 / 1.5 / scale)
    window.change_video_zoom(0)
    assert window.video_zoom_factor == 1 and not window.zoom_frame.isVisible()
    window.change_video_zoom(1)
    assert window.zoom_frame.isVisible()
    assert window.zoom_preview.isVisible()
    window.end_recording_extras()
    assert window.zoom_frame is None and window.zoom_preview is None


def test_live_zoom_preview_widget_is_click_through(qt_app):
    from PySide6.QtCore import Qt
    from screenkaffee.videoextras import LiveZoomPreview
    preview = LiveZoomPreview()
    assert preview.windowFlags() & Qt.WindowType.WindowTransparentForInput
    preview.show_area([100, 100, 200, 150], factor=2.0, scale=1)
    assert preview.isVisible() and preview.factor == 2.0
    preview.hide()


def test_screen_under_the_mouse_can_replace_the_chosen_screen():
    screens = [("links", (0, 0, 1920, 1080)), ("rechts", (1920, 0, 1920, 1080))]
    assert pick_screen(screens, (100, 100), "rechts", follow_mouse=False) == "rechts"
    assert pick_screen(screens, (100, 100), "rechts", follow_mouse=True) == "links"
    assert pick_screen(screens, (2000, 500), "links", follow_mouse=True) == "rechts"
    assert pick_screen(screens, (-50, 10), "links", follow_mouse=True) == "links"          # off every screen: keep the choice
    assert pick_screen([], (0, 0), "x", follow_mouse=True) == "x"
