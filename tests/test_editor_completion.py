import pytest
from PIL import Image, ImageDraw
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtTest import QTest
from screenkaffee.editor import EditorView


def visible_view(qt_app):
    view = EditorView(Image.new("RGB", (500, 400), "white"))
    view.resize(700, 600)
    view.show()
    qt_app.processEvents()
    return view


@pytest.mark.parametrize("corner", range(4))
def test_all_corner_handles_resize_and_undo(qt_app, corner):
    view = visible_view(qt_app)
    item = view.add_annotation({"kind": "rectangle", "color": "#ff0000", "width": 3, "points": [120, 100, 240, 180]})
    item.setSelected(True)
    original = item.serialize()
    before = QRectF(item.body_rect())
    anchor = [before.bottomRight(), before.bottomLeft(), before.topLeft(), before.topRight()][corner]
    start = view.mapFromScene(item.mapToScene(item.handles()[corner]))
    delta = QPointF(-20 if corner in (0, 3) else 20, -20 if corner < 2 else 20)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), start + delta.toPoint())
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=start + delta.toPoint())
    result = item.serialize()
    assert item.body_rect().width() == pytest.approx(140)
    assert item.body_rect().height() == pytest.approx(100)
    actual_anchor = [item.body_rect().bottomRight(), item.body_rect().bottomLeft(), item.body_rect().topLeft(), item.body_rect().topRight()][corner]
    assert actual_anchor == anchor
    view.undo.undo()
    assert item.serialize() == original
    view.undo.redo()
    assert item.serialize() == result
    view.close()


def test_selection_scaling_keeps_rotation_positions_and_one_undo(qt_app):
    view = visible_view(qt_app)
    items = [view.add_annotation({"kind": "rectangle", "color": "#ff0000", "width": 3,
             "points": [80, 80, 120, 100], "pos": [index * 100, 0], "rotation": 25}) for index in range(2)]
    for item in items:
        item.setSelected(True)
    before = [item.serialize() for item in items]
    distance = (items[1].sceneBoundingRect().center() - items[0].sceneBoundingRect().center()).manhattanLength()
    view.scale_selection(2)
    assert items[0].body_rect().width() == 80
    assert items[0].rotation() == 25
    assert (items[1].sceneBoundingRect().center() - items[0].sceneBoundingRect().center()).manhattanLength() == pytest.approx(distance * 2)
    view.undo.undo()
    assert [item.serialize() for item in items] == before
    view.close()


def test_speech_tail_drag_is_independent_and_reeditable(qt_app):
    view = visible_view(qt_app)
    item = view.add_annotation({"kind": "speech", "color": "#ff0000", "width": 3,
             "points": [100, 80, 300, 180], "text_width": 200, "text_height": 100, "text": "Hallo"})
    item.setSelected(True)
    body = QRectF(item.body_rect())
    tip = item.tail_tip()
    start = view.mapFromScene(item.mapToScene(tip))
    end = view.mapFromScene(item.mapToScene(tip + QPointF(60, 30)))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert item.body_rect() == body
    assert item.tail_tip() == tip + QPointF(60, 30)
    expected = view.render_image().tobytes()
    reloaded = EditorView(view.source)
    reloaded.load_project(view.project())
    assert reloaded.render_image().tobytes() == expected
    view.undo.undo()
    assert item.tail_tip() == tip
    view.close()


def test_text_shadow_glow_and_locked_magnifier_roundtrip(qt_app):
    view = EditorView(Image.new("RGB", (500, 400), "white"))
    text = {"kind": "text", "color": "#ff0000", "width": 2, "points": [70, 70, 270, 160],
            "text": "Kaffee", "font_size": 36, "text_width": 200, "text_height": 90}
    view.add_annotation(text)
    plain = view.render_image()
    view.undo.undo()
    view.add_annotation(dict(text, shadow_radius=4, shadow_offset=8, shadow_color="#000000", glow_radius=3, glow_color="#0000ff"))
    decorated = view.render_image()
    assert plain.tobytes() != decorated.tobytes()
    reloaded = EditorView(view.source)
    reloaded.load_project(view.project())
    assert reloaded.render_image().tobytes() == decorated.tobytes()
    source = Image.new("RGB", (500, 400), "red")
    ImageDraw.Draw(source).rectangle((250, 0, 499, 399), fill="blue")
    view = EditorView(source)
    item = view.add_annotation({"kind": "magnifier", "color": "#000000", "width": 2, "points": [60, 100, 160, 200], "zoom": 2})
    item.setSelected(True)
    view.toggle_magnifier_lock()
    assert "sample_box" in item.data
    item.setPos(250, 0)
    assert view.render_image().getpixel((360, 150)) == (255, 0, 0)
    item.setSelected(True)
    view.toggle_magnifier_lock()
    assert view.render_image().getpixel((360, 150)) == (0, 0, 255)


def test_stamp_resize_preserves_aspect_without_shift(qt_app):
    view = visible_view(qt_app)
    view.insert_stamp(Image.new("RGBA", (120, 60), "red"), QPointF(120, 100))
    item = view.annotations()[0]
    item.setSelected(True)
    start = view.mapFromScene(item.mapToScene(item.handles()[2]))
    end = start + QPointF(70, 20).toPoint()
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert item.body_rect().width() / item.body_rect().height() == pytest.approx(2)
    view.close()
