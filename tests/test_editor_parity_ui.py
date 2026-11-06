from PIL import Image
import pytest
from screenkaffee.effects import cut_out
from screenkaffee.editor import EditorWindow, CutOutCommand


def test_cut_out_geometry():
    image = Image.new("RGB", (10, 10))
    for y in range(10):
        for x in range(10):
            image.putpixel((x, y), (y * 20, x * 20, 0))
    out = cut_out(image, "y", 2, 5)
    assert out.size == (10, 7)
    assert out.getpixel((0, 1)) == (20, 0, 0) and out.getpixel((0, 2)) == (100, 0, 0)
    out = cut_out(image, "x", 0, 4)
    assert out.size == (6, 10) and out.getpixel((0, 0)) == (0, 80, 0)
    for bad in [(5, 5), (0, 10)]:
        with pytest.raises(ValueError):
            cut_out(image, "y", *bad)


def test_cut_out_moves_annotations_and_undoes(qt_app):
    window = EditorWindow(Image.new("RGB", (100, 100), "white"))
    view = window.view
    view.add_annotation({"kind": "rectangle", "color": "#ff0000", "width": 2, "points": [0, 60, 20, 80]})
    view.add_annotation({"kind": "rectangle", "color": "#00ff00", "width": 2, "points": [0, 5, 20, 15]})
    below, above = [a for a in view.annotations()]
    image = cut_out(view.source, "y", 20, 50)
    view.undo.push(CutOutCommand(view, image, "y", 20, 50))
    assert view.source.size == (100, 70) and below.pos().y() == -30 and above.pos().y() == 0
    view.undo.undo()
    assert view.source.size == (100, 100) and below.pos().y() == 0


def test_status_widgets_follow_zoom(qt_app):
    window = EditorWindow(Image.new("RGB", (200, 100), "white"))
    window.show()
    window.actual_size()
    assert window.zoom_label.text() == "100 %" and window.size_label.text().strip() == "200 × 100"
    window.zoom_to(200)
    assert round(window.view.zoom_percent()) == 200


def test_interactive_cutout_and_quick_style_controls(qt_app):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    window = EditorWindow(Image.new("RGB", (200, 150), "white"))
    window.resize(900, 700)
    window.show()
    qt_app.processEvents()

    # Interactive mouse cut-out removes horizontal strip (y: 30..80 -> height 100)
    window.cutout_action.trigger()
    assert window.view.tool == "cutout" and window.cutout_action.isChecked()
    view = window.view
    a, b = view.mapFromScene(QPointF(50, 30)), view.mapFromScene(QPointF(60, 80))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
    QTest.mouseMove(view.viewport(), b)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b)
    qt_app.processEvents()
    assert view.source.size == (200, 100)
    assert view.tool == "select" and not window.cutout_action.isChecked()

    # Quick-controls update selected annotation style immediately
    item = view.add_annotation({"kind": "arrow", "color": "#ff0000", "width": 4, "points": [10, 10, 90, 50]})
    item.setSelected(True)
    window.shadow_check.setChecked(True)
    assert item.data.get("shadow_radius") == 8
    window.line_style_combo.setCurrentIndex(window.line_style_combo.findData("dash"))
    assert item.data.get("line_style") == "dash"
    window.arrow_style_combo.setCurrentIndex(window.arrow_style_combo.findData("double"))
    assert item.data.get("arrow_style") == "double"
