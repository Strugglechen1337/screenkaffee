import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from screenkaffee.editor import EditorView, EditorWindow


def test_polygon_is_closed_unless_alt_keeps_it_open(qt_app):
    view = EditorView(Image.new("RGB", (200, 150), "white"))
    view.set_tool("polygon")
    view.polygon_points = [[10, 10], [100, 20], [60, 120]]
    view.finish_polygon()
    view.polygon_points = [[10, 10], [100, 20], [60, 120]]
    view.finish_polygon(keep_open=True)
    closed, opened = view.annotations()
    assert closed.data["closed"] is True and opened.data["closed"] is False
    view.polygon_points = [[10, 10], [50, 50]]
    view.finish_polygon()                                  # two points make no polygon
    assert len(view.annotations()) == 2 and view.polygon_points == []
    view.polygon_points = [[10, 10], [50, 50]]
    view.finish_polygon(keep_open=True)                    # but a two-point open line is fine
    assert len(view.annotations()) == 3


def polygon_window(qt_app):
    window = EditorWindow(Image.new("RGB", (640, 400), "white"))
    window.resize(1100, 700)
    window.show()
    window.tool_combo.setCurrentIndex(window.tool_combo.findData("polygon"))
    qt_app.processEvents()
    return window


def click_scene(window, qt_app, x, y, button=Qt.MouseButton.LeftButton, modifier=Qt.KeyboardModifier.NoModifier):
    from PySide6.QtTest import QTest
    view = window.view
    QTest.mouseClick(view.viewport(), button, modifier, view.mapFromScene(QPointF(x, y)))
    qt_app.processEvents()


def test_polygon_shows_a_live_preview_while_points_are_clicked(qt_app):
    from PySide6.QtTest import QTest
    window = polygon_window(qt_app)
    view = window.view
    assert view.preview is None
    click_scene(window, qt_app, 100, 100)
    assert view.preview is not None and len(view.polygon_points) == 1
    QTest.mouseMove(view.viewport(), view.mapFromScene(QPointF(300, 120)))
    assert view.preview.data["path"][-1] == pytest.approx([300, 120], abs=2)         # rubber band follows the mouse
    click_scene(window, qt_app, 300, 120)
    click_scene(window, qt_app, 250, 250)
    assert len(view.polygon_points) == 3 and view.annotations() == []                 # the preview is not a real item yet
    picture = view.viewport().grab().toImage()
    start = view.mapFromScene(QPointF(100, 100))
    assert picture.pixelColor(start.x(), start.y()).name() == "#ff5a36"                # corner dot is visible
    assert "Polygon" in window.statusBar().currentMessage()


def test_polygon_can_be_finished_by_right_click_double_click_and_enter(qt_app):
    from PySide6.QtTest import QTest
    for how in ("right", "double", "enter"):
        window = polygon_window(qt_app)
        view = window.view
        for x, y in ((100, 100), (300, 120), (250, 250)):
            click_scene(window, qt_app, x, y)
        if how == "right":
            click_scene(window, qt_app, 250, 250, button=Qt.MouseButton.RightButton)
        elif how == "double":
            QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, view.mapFromScene(QPointF(250, 250)))
        else:
            QTest.keyClick(view, Qt.Key.Key_Return)
        qt_app.processEvents()
        shapes = view.annotations()
        assert len(shapes) == 1 and shapes[0].data["kind"] == "polygon", how
        assert shapes[0].data["closed"] is True and len(shapes[0].data["path"]) >= 3, how
        assert view.preview is None and view.polygon_points == [], how
        rendered = view.render_image().convert("RGB").tobytes()
        assert any(rendered[i:i + 3] == b"\xff\x5a\x36" for i in range(0, len(rendered), 3)), how
        window.discard_changes = True                      # otherwise closing would ask whether to save
        window.close()


def test_alt_keeps_the_polygon_open_and_escape_cancels_it(qt_app):
    window = polygon_window(qt_app)
    view = window.view
    for x, y in ((100, 100), (300, 120), (250, 250)):
        click_scene(window, qt_app, x, y)
    click_scene(window, qt_app, 250, 250, button=Qt.MouseButton.RightButton, modifier=Qt.KeyboardModifier.AltModifier)
    assert [a.data["closed"] for a in view.annotations()] == [False]
    for x, y in ((50, 50), (80, 90)):
        click_scene(window, qt_app, x, y)
    from PySide6.QtTest import QTest
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert view.tool == "select" and view.preview is None and view.polygon_points == [] and len(view.annotations()) == 1
    window.discard_changes = True
    window.close()


def test_rotate_buttons_turn_the_output_and_undo(qt_app):
    window = EditorWindow(Image.new("RGB", (200, 100), "white"))
    assert window.view.render_image().size == (200, 100)
    assert window.rotate_image(90) == 90
    assert window.view.render_image().size == (100, 200)
    window.rotate_image(90)
    window.rotate_image(90)
    assert window.view.effects["rotation"] == -90 and window.view.render_image().size == (100, 200)
    assert window.rotate_image(90) == 0 and window.view.render_image().size == (200, 100)
    window.view.undo.undo()
    assert window.view.effects["rotation"] == -90
    names = [a.text() for a in window.commandbar.actions()]
    assert "Drehen" in names
    assert [a.text() for a in window.rotate_menu.actions()] == ["Rechts drehen", "Links drehen"]


def test_alt_drag_moves_a_speech_bubble_text_but_not_its_pointer(qt_app):
    view = EditorView(Image.new("RGB", (400, 300), "white"))
    view.add_annotation({"kind": "speech", "color": "#000000", "text": "Hi", "points": [50, 50, 250, 110], "text_width": 200,
                         "text_height": 60, "tail": [70, 130]})
    item = view.annotations()[0]
    start = item.pos()
    tip_before = item.pos() + item.tail_tip()
    item.setPos(start + QPointF(40, 25))
    item.move_text_only(start, item.pos())
    assert item.pos() == start + QPointF(40, 25)
    assert item.pos() + item.tail_tip() == tip_before
    view.undo.undo()                                       # one undo step brings back both
    assert item.pos() == start and item.tail_tip() == QPointF(70, 130)


def test_plain_drag_moves_text_and_pointer_together(qt_app):
    view = EditorView(Image.new("RGB", (400, 300), "white"))
    view.add_annotation({"kind": "speech", "color": "#000000", "text": "Hi", "points": [50, 50, 250, 110], "text_width": 200,
                         "text_height": 60, "tail": [70, 130]})
    item = view.annotations()[0]
    tip_before = item.pos() + item.tail_tip()
    from screenkaffee.editor import MoveCommand
    start = item.pos()
    item.setPos(start + QPointF(40, 25))
    view.undo.push(MoveCommand(item, start, item.pos()))
    assert (item.pos() + item.tail_tip()) - tip_before == QPointF(40, 25)


def test_alt_shift_closes_every_editor_without_asking(qt_app, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    windows = [EditorWindow(Image.new("RGB", (60, 40), color)) for color in ("red", "green", "blue")]
    for window in windows:
        window.show()
        window.view.add_annotation({"kind": "rectangle", "color": "#ff0000", "width": 3, "points": [5, 5, 30, 30]})
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("Es darf nicht nachgefragt werden"))
    monkeypatch.setattr(QApplication, "keyboardModifiers",
                        staticmethod(lambda: Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier))
    assert windows[0].close()
    qt_app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not any(isinstance(w, EditorWindow) and w.isVisible() for w in QApplication.topLevelWidgets())


def test_without_the_modifiers_unsaved_work_is_still_protected(qt_app, monkeypatch):
    window = EditorWindow(Image.new("RGB", (60, 40), "red"))
    window.show()
    window.view.add_annotation({"kind": "rectangle", "color": "#ff0000", "width": 3, "points": [5, 5, 30, 30]})
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.Cancel)
    assert not window.close() and asked == [1] and window.isVisible()
    window.discard_changes = True
    assert window.close()


def test_ctrl_drag_duplicates_and_alt_drag_moves_magnifier_glass_and_stamp_restore(qt_app):
    view = EditorView(Image.new("RGB", (300, 200), "#112233"))
    view.add_annotation({"kind": "rectangle", "color": "#ff0000", "width": 4, "points": [20, 20, 80, 60]})
    rect_item = view.annotations()[0]
    rect_item.duplicate_to(QPointF(0, 0), QPointF(50, 30))
    assert len(view.annotations()) == 2
    assert view.annotations()[0].pos() == QPointF(0, 0) and view.annotations()[1].pos() == QPointF(50, 30)
    view.undo.undo()
    assert len(view.annotations()) == 1

    view.add_annotation({"kind": "magnifier", "color": "#00ff00", "width": 3, "points": [40, 40, 120, 120], "zoom": 2})
    mag = view.annotations()[-1]
    mag.move_magnifier_glass_only(QPointF(0, 0), QPointF(30, 20))
    assert mag.pos() == QPointF(30, 20) and "sample_box" in mag.data
    view.undo.undo()
    assert mag.pos() == QPointF(0, 0) and "sample_box" not in mag.data

    view.insert_stamp(Image.new("RGBA", (40, 25), (255, 0, 0, 255)), QPointF(10, 10))
    stamp = view.annotations()[-1]
    stamp.setSelected(True)
    stamp.data["points"] = [10, 10, 90, 75]
    assert view.restore_stamp_size() is True
    assert stamp.data["points"] == [10, 10, 50, 35]

    view.set_tool("rectangle")
    view.cover_background = True
    view.color = view.color_under(QPointF(15, 15))
    view.start = QPointF(10, 10)
    data = view.drawing_data(QPointF(60, 40))
    assert data["fill"] == "#112233" and data["color"] == "#112233"


def test_rotation_is_shown_live_in_the_view(qt_app):
    window = EditorWindow(Image.new("RGB", (100, 200), "white"), "t.png")
    window.show()
    window.rotate_image(90)
    mapped = window.view.transform().mapRect(window.view.crop_rect)
    assert mapped.width() > mapped.height()  # Ansicht ist gedreht (quer statt hochkant)
    window.view.undo.undo()
    mapped = window.view.transform().mapRect(window.view.crop_rect)
    assert mapped.width() < mapped.height()


def test_flip_color_and_border_effects_are_shown_live(qt_app):
    from PySide6.QtGui import QImage
    from screenkaffee.editor import EffectsCommand
    window = EditorWindow(Image.new("RGB", (100, 100), (200, 40, 40)), "t.png")
    window.show()
    view = window.view
    view.undo.push(EffectsCommand(view, dict(view.effects, flip_horizontal=True, grayscale=True, border_width=6)))
    assert view.transform().m11() < 0  # gespiegelt
    pixel = view.base.pixmap().toImage().pixelColor(50, 50)
    assert pixel.red() == pixel.green() == pixel.blue()  # Graustufen live
    view.render_image()  # Export darf die Effekte nicht doppelt anwenden
    pixel = view.base.pixmap().toImage().pixelColor(50, 50)
    assert pixel.red() == pixel.green() == pixel.blue()
    out = view.render_image().convert("RGB").getpixel((50, 50))
    assert out[0] == out[1] == out[2]
    view.undo.undo()
    assert view.transform().m11() > 0
    assert view.base.pixmap().toImage().pixelColor(50, 50).red() == 200


def test_rotated_view_keeps_handle_scale_and_replaced_source_is_shown(qt_app):
    from screenkaffee.editor import EffectsCommand, SourceCommand
    window = EditorWindow(Image.new("RGB", (100, 50), (10, 20, 30)), "t.png")
    window.show()
    view = window.view
    view.undo.push(EffectsCommand(view, dict(view.effects, rotation=90, grayscale=True)))
    assert abs(view.view_scale() - abs(view.transform().m12())) < 1e-6
    view.undo.push(SourceCommand(view, Image.new("RGB", (100, 50), (200, 0, 0))))
    pixel = view.base.pixmap().toImage().pixelColor(5, 5)
    assert pixel.red() == pixel.green() == pixel.blue() > 0  # neues Bild, Graustufen weiter aktiv


def test_crop_handle_cursor_turns_with_the_rotated_view(qt_app):
    from screenkaffee.editor import EffectsCommand
    window = EditorWindow(Image.new("RGB", (100, 50), "white"), "t.png")
    window.show()
    view = window.view
    assert view.crop_handle_cursor("ml") == Qt.CursorShape.SizeHorCursor
    assert view.crop_handle_cursor("tl") == Qt.CursorShape.SizeFDiagCursor
    view.undo.push(EffectsCommand(view, dict(view.effects, rotation=90)))
    assert view.crop_handle_cursor("ml") == Qt.CursorShape.SizeVerCursor
    assert view.crop_handle_cursor("tl") == Qt.CursorShape.SizeBDiagCursor
    assert view.crop_handle_cursor(None) == Qt.CursorShape.CrossCursor


def test_output_preview_is_live_and_non_modal(qt_app):
    from PySide6.QtTest import QTest
    from screenkaffee.editor import EffectsCommand
    window = EditorWindow(Image.new("RGB", (100, 50), "white"), "t.png")
    window.show()
    assert getattr(window, "_output_preview", None) is None
    window.apply_effect_values({"border_width": 4, "border_color": "#112233"})
    assert getattr(window, "_output_preview", None) is None  # öffnet sich nicht ungefragt bei jedem Rahmendialog
    dialog = window.preview_output()
    assert dialog.isVisible() and not dialog.isModal()
    assert window.preview_output() is dialog  # kein zweites Fenster
    assert "100 × 50" in dialog.findChildren(type(dialog.findChildren(__import__("PySide6.QtWidgets").QtWidgets.QLabel)[0]))[1].text()
    window.view.undo.push(EffectsCommand(window.view, dict(window.view.effects, padding=20)))
    QTest.qWait(400)
    texts = " ".join(l.text() for l in dialog.findChildren(__import__("PySide6.QtWidgets").QtWidgets.QLabel))
    assert "140 × 90" in texts  # Vorschau hat sich selbst aktualisiert
    dialog.close()
    qt_app.processEvents()
    window.view.undo.undo()  # darf nach dem Schließen nicht abstürzen
    QTest.qWait(300)


def test_cutout_with_gap_shifts_annotations_by_actual_image_shrinkage_and_rotated_step_stays_upright(qt_app):
    from PySide6.QtCore import QPointF
    from PySide6.QtTest import QTest
    from screenkaffee.editor import CutOutCommand
    from screenkaffee.effects import cut_out
    window = EditorWindow(Image.new("RGB", (120, 120), "white"), "t.png")
    window.show()
    view = window.view
    item = view.add_annotation({"kind": "rectangle", "color": "#ff0000", "width": 3, "points": [10, 80, 40, 100]})
    cut_img = cut_out(view.source, "y", 20, 60, gap=12)
    assert cut_img.height == 92  # 120 - (60 - 20) + 12 = 92 (28 px geschrumpft)
    view.undo.push(CutOutCommand(view, cut_img, "y", 20, 60))
    assert item.pos().y() == -28
    view.undo.undo()
    assert item.pos().y() == 0

    # Adding a step in a 90°-rotated view counter-rotates the item so the label stays upright
    window.rotate_image(90)
    window.select_tool("step")
    pt = view.mapFromScene(QPointF(50, 50))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pt)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pt)
    steps = [a for a in view.annotations() if a.data["kind"] == "step"]
    assert len(steps) == 1 and steps[0].data.get("rotation") == -90


def test_blur_annotation_caches_filtered_qimage_until_moved(qt_app):
    window = EditorWindow(Image.new("RGB", (120, 100), "#336699"), "t.png")
    window.show()
    view = window.view
    blur_item = view.add_annotation({"kind": "blur", "color": "#000000", "width": 4, "points": [10, 10, 60, 50]})
    _ = view.render_image()
    key1 = getattr(blur_item, "_effect_cache_key", None)
    cached1 = getattr(blur_item, "_effect_cache_qimage", None)
    assert key1 is not None and cached1 is not None
    _ = view.render_image()
    assert getattr(blur_item, "_effect_cache_qimage", None) is cached1
    blur_item.setPos(5, 5)
    _ = view.render_image()
    assert getattr(blur_item, "_effect_cache_key", None) != key1

