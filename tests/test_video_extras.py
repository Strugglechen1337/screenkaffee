from PySide6.QtCore import QPoint
from screenkaffee.videoextras import ClickEffect, preset_region, RECORD_SIZES


def test_preset_regions_are_centred_even_and_fit_the_screen():
    assert preset_region([0, 0, 2560, 1440], (1280, 720)) == [640, 360, 1280, 720]
    assert preset_region([-1920, 0, 1920, 1080], (1280, 720)) == [-1600, 180, 1280, 720]
    assert preset_region([0, 0, 1000, 600], (1920, 1080)) == [0, 0, 1000, 600]
    region = preset_region([0, 0, 1366, 769], (854, 480))
    assert region[2] % 2 == 0 and region[3] % 2 == 0
    assert [label for label, _ in RECORD_SIZES][0] == "1920 × 1080"


def test_click_effect_shows_a_ring_per_press_and_fades(qt_app):
    now = [0.0]
    buttons = [(False, False)]
    effect = ClickEffect(button_state=lambda: buttons[0], position=lambda: QPoint(300, 200), clock=lambda: now[0])
    effect.tick()
    assert effect.progress() is None and not effect.isVisible()
    buttons[0] = (True, False)
    effect.tick()
    assert effect.isVisible() and effect.progress() == 0 and effect.color.name() == "#ffcc00"
    assert (effect.pos().x() + 60, effect.pos().y() + 60) == (300, 200)
    effect.tick()                                           # button still held: no second ring
    now[0] = .2
    assert 0 < effect.progress() < 1
    now[0] = 1.0
    effect.tick()
    assert effect.progress() is None and not effect.isVisible()
    buttons[0] = (False, True)
    effect.tick()
    assert effect.color.name() == "#ff5a36" and effect.isVisible()
    effect.stop()
    assert not effect.isVisible()


def test_click_effect_is_click_through(qt_app):
    from PySide6.QtCore import Qt
    effect = ClickEffect()
    assert effect.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert effect.windowFlags() & Qt.WindowType.WindowStaysOnTopHint


class FakeRecorder:
    def __init__(self):
        self.active = False
        self.started = []

    def start(self, folder, **options):
        self.started.append(options)
        self.active = True


def test_recording_starts_extras_and_stops_at_the_time_limit(qt_app, tmp_path, monkeypatch):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    window = MainWindow(Workspace(tmp_path))
    window.recorder = FakeRecorder()
    window.pending_video_options = {"fps": 30, "quality": "high", "click_effect": True, "max_minutes": 2}
    window.begin_video_recording([0, 0, 640, 360])
    options = window.recorder.started[0]
    assert "click_effect" not in options and "max_minutes" not in options and options["region"] == [0, 0, 640, 360]
    assert window.click_effect is not None and window.stop_timer.isActive() and window.stop_timer.interval() == 120_000
    stopped = []
    monkeypatch.setattr(window, "toggle_video", lambda: stopped.append(True))
    window.stop_recording_by_limit()
    assert stopped == [True]
    window.end_recording_extras()
    assert window.click_effect is None and window.stop_timer is None
    window.recorder.active = False
    window.stop_recording_by_limit()
    assert stopped == [True]


def test_no_extras_without_the_options(qt_app, tmp_path):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    window = MainWindow(Workspace(tmp_path))
    window.recorder = FakeRecorder()
    window.pending_video_options = {"fps": 30}
    window.begin_video_recording(None)
    assert getattr(window, "click_effect", None) is None and getattr(window, "stop_timer", None) is None
