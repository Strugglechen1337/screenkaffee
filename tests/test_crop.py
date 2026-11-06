from PIL import Image, ImageDraw
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from screenkaffee.editor import CropCommand, EditorWindow

OUTSIDE = (345, 100)     # right of the crop (x > 330) but still inside the enlarged view
BLUE = "#3a78c2"
HIDDEN = "#202020"


def make_window(qt_app):
    image = Image.new("RGB", (640, 400), BLUE)
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 300, 200), fill="white")
    draw.rectangle((400, 250, 600, 380), fill="#e0a030")
    window = EditorWindow(image, "bild.png")
    window.resize(1100, 700)
    window.show()
    window.actual_size()  # 100 %: Szenen- und Pixelkoordinaten stimmen exakt überein (keine Rundung je nach Fenstergröße)
    qt_app.processEvents()
    return window


def screen_pixel(window, x, y):
    view = window.view
    point = view.mapFromScene(QPointF(x, y))
    picture = view.viewport().grab().toImage()
    assert 0 <= point.x() < picture.width() and 0 <= point.y() < picture.height(), "Punkt liegt außerhalb des sichtbaren Bereichs"
    return picture.pixelColor(point.x(), point.y()).name()


def drag_crop(window, qt_app, start, end):
    view = window.view
    window.tool_combo.setCurrentIndex(window.tool_combo.findData("crop"))
    qt_app.processEvents()
    a, b = view.mapFromScene(QPointF(*start)), view.mapFromScene(QPointF(*end))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
    QTest.mouseMove(view.viewport(), b)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b)
    qt_app.processEvents()


def test_crop_drag_snaps_each_scene_edge_to_nearest_pixel(qt_app):
    window = make_window(qt_app)
    view = window.view
    a = view.mapToScene(view.mapFromScene(QPointF(30, 30)))
    b = view.mapToScene(view.mapFromScene(QPointF(330, 230)))
    expected = (round(a.x()), round(a.y()), round(b.x()) - round(a.x()), round(b.y()) - round(a.y()))
    drag_crop(window, qt_app, (30, 30), (330, 230))
    rect = view.crop_rect
    assert (rect.x(), rect.y(), rect.width(), rect.height()) == expected


def test_dragging_a_crop_hides_everything_outside_and_refits_the_view(qt_app):
    window = make_window(qt_app)
    before = window.view.zoom_percent()
    assert screen_pixel(window, *OUTSIDE) == BLUE
    drag_crop(window, qt_app, (30, 30), (330, 230))
    rect = window.view.crop_rect
    assert (rect.x(), rect.y(), rect.width(), rect.height()) == (30, 30, 300, 200)
    assert window.view.render_image().size == (300, 200)
    assert screen_pixel(window, *OUTSIDE) == HIDDEN
    assert screen_pixel(window, 100, 100) == "#ffffff"
    assert window.view.zoom_percent() > before                  # the smaller area is enlarged to fill the view
    assert window.size_label.text().strip() == "300 × 200"
    assert window.view.tool == "select"


def test_undo_and_redo_bring_the_hidden_part_back(qt_app):
    window = make_window(qt_app)
    drag_crop(window, qt_app, (30, 30), (330, 230))
    window.view.undo.undo()
    qt_app.processEvents()
    assert window.view.crop_rect.width() == 640 and screen_pixel(window, *OUTSIDE) == BLUE
    assert window.size_label.text().strip() == "640 × 400"
    window.view.undo.redo()
    qt_app.processEvents()
    assert window.view.crop_rect.width() == 300 and screen_pixel(window, *OUTSIDE) == HIDDEN


def test_annotations_outside_the_crop_are_not_exported_or_shown(qt_app):
    window = make_window(qt_app)
    window.view.add_annotation({"kind": "rectangle", "color": "#ff0000", "width": 6, "points": [335, 60, 350, 150]})
    assert screen_pixel(window, 335, 100) == "#ff0000"
    drag_crop(window, qt_app, (30, 30), (330, 230))
    assert screen_pixel(window, 335, 100) == HIDDEN
    exported = window.view.render_image().convert("RGB")
    pixels = exported.get_flattened_data() if hasattr(exported, "get_flattened_data") else exported.getdata()
    assert exported.size == (300, 200) and (255, 0, 0) not in set(pixels)


def test_crop_can_be_repeated_inside_the_current_crop_only(qt_app):
    window = make_window(qt_app)
    drag_crop(window, qt_app, (30, 30), (330, 230))
    drag_crop(window, qt_app, (60, 60), (200, 150))
    rect = window.view.crop_rect
    assert (rect.x(), rect.y(), rect.width(), rect.height()) == (60, 60, 140, 90)
    before = window.view.crop_rect
    drag_crop(window, qt_app, (500, 300), (600, 380))        # starts outside the crop: ignored
    assert window.view.crop_rect == before
    window.view.undo.undo()
    window.view.undo.undo()
    assert window.view.crop_rect.width() == 640


def test_crop_command_keeps_float_rects_exact(qt_app):
    window = make_window(qt_app)
    window.view.undo.push(CropCommand(window.view, window.view.crop_rect.adjusted(10, 20, -30, -40)))
    assert window.view.render_image().size == (600, 340)


def test_crop_handles_outside_margin_and_workspace_thumbnail_update(qt_app, tmp_path):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    ws = Workspace(tmp_path)
    ident = ws.add(Image.new("RGB", (640, 400), BLUE), "Zuschnitt-Test")
    main = MainWindow(ws)
    main.open_editor(ident)
    editor = main.editors[0]
    editor.resize(1100, 700)
    editor.show()
    qt_app.processEvents()

    # Clicking "Zuschneiden" activates crop mode even after view.set_tool("select")
    editor.crop_action.trigger()
    assert editor.view.tool == "crop" and editor.crop_action.isChecked()
    editor.view.set_tool("select")
    assert editor.view.tool == "select" and not editor.crop_action.isChecked()
    editor.crop_action.trigger()
    assert editor.view.tool == "crop" and editor.crop_action.isChecked()

    # Dragging the right-edge crop handle ("mr") at (640, 200) inward to (400, 200)
    view = editor.view
    a, b = view.mapFromScene(QPointF(640, 200)), view.mapFromScene(QPointF(400, 200))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
    QTest.mouseMove(view.viewport(), b)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b)
    qt_app.processEvents()
    assert (view.crop_rect.x(), view.crop_rect.y(), view.crop_rect.width(), view.crop_rect.height()) == (0, 0, 400, 400)

    # Starting a crop drag slightly outside the top-left border (-10, -10) to (250, 180)
    editor.crop_action.trigger()
    a, b = view.mapFromScene(QPointF(-10, -10)), view.mapFromScene(QPointF(250, 180))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
    QTest.mouseMove(view.viewport(), b)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b)
    qt_app.processEvents()
    assert (view.crop_rect.x(), view.crop_rect.y(), view.crop_rect.width(), view.crop_rect.height()) == (0, 0, 250, 180)

    # Saving project updates workspace thumbnail and dimensions
    assert editor.save_project()
    row = ws.get(ident)
    assert (row["width"], row["height"]) == (250, 180)
    assert ws.thumbnail_path(ident).name == f"{ident}.thumb.png"
    ws.close()
