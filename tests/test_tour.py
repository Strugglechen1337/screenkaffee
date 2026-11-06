"""Erster-Start-Tour durch die besten Funktionen."""
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtTest import QTest

from screenkaffee import tour


def test_steps_cover_the_best_features_and_use_configured_keys():
    steps = tour.steps({"hotkeys": {"region": "Print", "draw": "Ctrl+Alt+D"}})
    keys = [step[0] for step in steps]
    assert keys[0] == "welcome" and keys[-1] == "version"
    assert {"capture", "palette", "compare", "editor", "beautify", "video", "steps", "livedraw"} <= set(keys)
    texts = {step[0]: step[3] for step in steps}
    assert "Druck" in texts["capture"] and "Strg+Alt+D" in texts["livedraw"]
    assert "Vergleichen" in texts["compare"] and "Strg+Klick" in texts["compare"]
    # ohne Tasten: sinnvoller Hinweis statt leerer Taste
    plain = {step[0]: step[3] for step in tour.steps({"hotkeys": {}})}
    assert "<b></b>" not in plain["capture"] and "Einstellungen" in plain["capture"]


def test_bubble_prefers_below_then_above_and_stays_inside():
    area = QRect(0, 0, 1000, 700)
    size = QSize(300, 150)
    below = tour.bubble_position(QRect(100, 50, 120, 30), size, area)
    assert below.y() >= 50 + 30 + tour.MARGIN and below.x() >= tour.GAP
    above = tour.bubble_position(QRect(400, 640, 80, 40), size, area)
    assert above.y() + size.height() <= 640 - tour.MARGIN
    middle = tour.bubble_position(None, size, area)
    assert abs(middle.x() - 350) <= 1 and abs(middle.y() - 275) <= 1
    edge = tour.bubble_position(QRect(980, 50, 20, 20), size, area)
    assert edge.x() + size.width() <= area.right() - tour.GAP + 1


def test_tour_walks_through_all_steps_and_remembers(qt_app, tmp_path, monkeypatch):
    from tests.test_pin_setup_crash import window_for
    window = window_for(tmp_path, monkeypatch)
    window.show()
    overlay = window.start_tour()
    assert overlay.isVisible() and overlay.current_key() == "welcome"
    assert overlay.target_rect() is None and not overlay.back_button.isVisibleTo(overlay)
    keys = []
    for _ in range(len(overlay.tour_steps) - 1):
        overlay.next_button.click()
        keys.append(overlay.current_key())
        if overlay.current_key() == "compare":
            assert "vergleichen" in overlay.title.text().lower()
            assert overlay.target_rect() is not None              # Aufnahmeliste wird hervorgehoben
            bubble = overlay.bubble.geometry()
            assert overlay.rect().contains(bubble)
    assert "compare" in keys and overlay.next_button.text() == "Los geht’s!"
    overlay.back_button.click()
    assert overlay.index == len(overlay.tour_steps) - 2
    QTest.keyClick(overlay, Qt.Key.Key_Right)
    QTest.keyClick(overlay, Qt.Key.Key_Return)                     # letzter Schritt: beenden
    assert window.settings["tour_done"] and window.tour is None
    window.close()


def test_tour_can_be_cancelled_and_reopened_from_menu(qt_app, tmp_path, monkeypatch):
    from tests.test_pin_setup_crash import window_for
    from screenkaffee.palette import collect
    window = window_for(tmp_path, monkeypatch)
    window.show()
    entries = dict(collect(window.more_menu))
    action = next(a for label, a in entries.items() if label.endswith("Tour: die besten Funktionen …"))
    action.trigger()
    overlay = window.tour
    assert overlay is not None and overlay.isVisible()
    QTest.keyClick(overlay, Qt.Key.Key_Escape)
    assert window.settings["tour_done"] and window.tour is None
    window.close()
