import pytest
from PIL import Image
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from screenkaffee.editor import EditorView, Annotation, qimage_to_pil


def scene_pixels(view):
    image = QImage(300, 220, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    view.scene().render(painter, QRectF(0, 0, 300, 220), view.crop_rect)
    painter.end()
    return qimage_to_pil(image)


@pytest.mark.parametrize("tool", ["arrow", "line", "rectangle", "ellipse", "freehand", "bracket", "highlight", "redact", "spotlight", "magnifier", "blur", "pixelate"])
def test_drag_previews_actual_tool_without_committing(qt_app, tool):
    view = EditorView(Image.new("RGB", (300, 220), "white"))
    view.resize(500, 400)
    view.show()
    qt_app.processEvents()
    view.set_tool(tool)
    start = view.mapFromScene(QPointF(40, 40))
    end = view.mapFromScene(QPointF(210, 150))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(view.viewport(), end)
    qt_app.processEvents()
    assert isinstance(view.preview, Annotation)
    assert view.preview.data["kind"] == tool
    assert not view.annotations()
    assert view.undo.count() == 0
    assert view.project()["annotations"] == []
    assert view.render_image().convert("RGB").tobytes() == view.source.tobytes()
    draft = scene_pixels(view)
    if tool in ("arrow", "line"):
        assert draft.getpixel((125, 95)) != (255, 255, 255)
        assert draft.getpixel((40, 145)) == (255, 255, 255)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert view.preview is None
    assert view.undo.count() == 1
    assert len(view.annotations()) == 1
    assert scene_pixels(view).tobytes() == draft.tobytes()
    view.undo.undo()
    assert not view.annotations()
    view.close()


def test_escape_or_tool_change_discards_draft(qt_app):
    view = EditorView(Image.new("RGB", (300, 220), "white"))
    view.resize(500, 400)
    view.show()
    qt_app.processEvents()
    for cancel in (lambda: QTest.keyClick(view, Qt.Key.Key_Escape), lambda: view.set_tool("ellipse")):
        view.set_tool("arrow")
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(QPointF(40, 40)))
        QTest.mouseMove(view.viewport(), view.mapFromScene(QPointF(180, 120)))
        cancel()
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=view.mapFromScene(QPointF(180, 120)))
        assert view.preview is None and view.start is None
        assert view.undo.count() == 0 and not view.annotations()
    view.close()


def test_freehand_loop_is_kept_when_released_at_start(qt_app):
    view = EditorView(Image.new("RGB", (300, 220), "white"))
    view.resize(500, 400)
    view.show()
    qt_app.processEvents()
    view.set_tool("freehand")
    origin = view.mapFromScene(QPointF(40, 40))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=origin)
    for x, y in ((180, 40), (180, 160), (40, 160), (40, 40)):
        QTest.mouseMove(view.viewport(), view.mapFromScene(QPointF(x, y)))
    draft = scene_pixels(view)
    assert draft.getpixel((110, 40)) != (255, 255, 255)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=origin)
    assert len(view.annotations()) == 1
    assert len(view.annotations()[0].data["path"]) >= 5
    assert scene_pixels(view).tobytes() == draft.tobytes()
    view.close()


def test_editor_commands_stay_visible_above_image(qt_app):
    from screenkaffee.editor import EditorWindow
    from PySide6.QtWidgets import QToolButton
    window = EditorWindow(Image.new("RGB", (300, 220), "white"))
    window.show()
    qt_app.processEvents()
    assert window.commandbar.isVisible()
    assert window.commandbar.geometry().bottom() < window.tools.geometry().top()
    actions = {a.text(): a for a in window.commandbar.actions()}
    assert {"Bild kopieren", "Projekt speichern", "Zuschneiden", "Größe / Effekte"} <= actions.keys()
    assert not actions["Zuschneiden"].icon().isNull()
    actions["Zuschneiden"].trigger()
    assert window.view.tool == "crop"
    assert window.palette_actions["crop"].isChecked()
    assert all(isinstance(b, QToolButton) for b in window.palette_actions.values())
    window.view.undo.setClean()
    window.close()
