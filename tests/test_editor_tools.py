import json
from PIL import Image
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication
from screenkaffee.editor import EditorView, EditorWindow, OPEN_EDITORS


def rect_item(view, x1, y1, x2, y2):
    view.add_annotation({"kind": "rectangle", "color": "#ff0000", "width": 3, "points": [x1, y1, x2, y2]})
    return view.annotations()[-1]


def as_tuple(point):
    return (round(point.x(), 6), round(point.y(), 6))


def test_magnet_aligns_edges_centres_and_canvas(qt_app):
    view = EditorView(Image.new("RGB", (400, 300), "white"))
    rect_item(view, 100, 100, 200, 150)
    mover = rect_item(view, 10, 10, 60, 40)                     # 50 x 30 body
    assert as_tuple(view.snap_position(mover, QPointF(93, 95))) == (90, 90)     # left -> 100, top -> 100
    assert as_tuple(view.snap_position(mover, QPointF(250, 200))) == (250, 200)  # nothing within reach
    assert as_tuple(view.snap_position(mover, QPointF(-12, -13))) == (-10, -10)  # canvas edges
    assert as_tuple(view.snap_position(mover, QPointF(93, 95), threshold=2)) == (93, 95)


def test_magnet_never_moves_items_programmatically(qt_app):
    view = EditorView(Image.new("RGB", (400, 300), "white"))
    rect_item(view, 100, 100, 200, 150)
    item = rect_item(view, 10, 10, 60, 40)
    item.setPos(QPointF(93, 95))
    assert as_tuple(item.pos()) == (93, 95)


def test_ctrl_makes_shapes_square_and_alt_reads_the_pixel_colour(qt_app):
    source = Image.new("RGB", (200, 100), "white")
    source.putpixel((20, 30), (10, 200, 30))
    view = EditorView(source)
    view.set_tool("rectangle")
    view.start = QPointF(10, 10)
    assert view.drawing_data(QPointF(60, 30), square=False)["points"] == [10, 10, 60, 30]
    assert view.drawing_data(QPointF(60, 30), square=True)["points"] == [10, 10, 60, 60]
    assert view.drawing_data(QPointF(0, 5), square=True)["points"] == [10, 10, 0, 0]
    view.set_tool("arrow")
    view.start = QPointF(10, 10)
    assert view.drawing_data(QPointF(60, 30), square=True)["points"] == [10, 10, 60, 30]
    assert view.color_under(QPointF(20.4, 30.2)) == "#0ac81e"
    assert view.color_under(QPointF(-5, 9999)) == "#ffffff"


def test_ctrl_plus_minus_resizes_text_and_steps_with_undo(qt_app):
    window = EditorWindow(Image.new("RGB", (300, 200), "white"))
    view = window.view
    view.add_annotation({"kind": "text", "color": "#000000", "text": "Hallo", "points": [10, 10, 100, 40], "text_width": 200,
                         "text_height": 60, "font_size": 22})
    view.add_annotation({"kind": "step", "color": "#0000ff", "text": "1", "points": [150, 20, 0, 0], "text_width": 46, "text_height": 46})
    text, step = view.annotations()
    text.setSelected(True)
    step.setSelected(True)
    assert window.change_item_size(1)
    assert (text.data["font_size"], step.data["text_width"], step.data["text_height"]) == (24, 50, 50)
    window.change_item_size(-1)
    window.change_item_size(-1)
    assert (text.data["font_size"], step.data["text_width"]) == (20, 42)
    view.undo.undo()
    assert text.data["font_size"] == 22 and step.data["text_width"] == 46
    view.scene().clearSelection()
    before = view.font_size
    assert window.change_item_size(1) is False
    assert view.font_size == before + 2


def test_auto_save_writes_the_project_after_each_change(qt_app, tmp_path):
    project = tmp_path / "bild.screenkaffee.json"
    window = EditorWindow(Image.new("RGB", (200, 120), "white"), project_path=project)
    window.view.add_annotation({"kind": "rectangle", "color": "#ff0000", "width": 3, "points": [10, 10, 80, 60]})
    assert not project.exists()
    window.auto_save_action.setChecked(True)
    window.view.add_annotation({"kind": "ellipse", "color": "#00ff00", "width": 3, "points": [20, 20, 90, 70]})
    kinds = [a["kind"] for a in json.loads(project.read_text(encoding="utf-8"))["annotations"]]
    assert kinds == ["rectangle", "ellipse"] and window.view.undo.isClean()


def test_copy_and_close_everywhere_handles_all_open_editors(qt_app):
    from PySide6.QtCore import QCoreApplication, QEvent
    first = EditorWindow(Image.new("RGB", (60, 40), "red"))
    second = EditorWindow(Image.new("RGB", (80, 50), "blue"))
    other = EditorWindow(Image.new("RGB", (30, 20), "green"))
    for window in (first, second, other):
        window.show()
    closed = []
    for window in (first, second):
        window.destroyed.connect(lambda *_: closed.append(1))
    first.copy_and_close(everywhere=False)
    qt_app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert len(closed) == 1 and second.isVisible() and other.isVisible()
    second.copy_and_close(everywhere=True)
    qt_app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert len(closed) == 2 and not any(isinstance(w, EditorWindow) and w.isVisible() for w in QApplication.topLevelWidgets())


def test_open_image_file_opens_a_new_editor(qt_app, tmp_path):
    path = tmp_path / "fremd.png"
    Image.new("RGB", (123, 45), "green").save(path)
    window = EditorWindow(Image.new("RGB", (20, 20), "white"))
    opened = window.open_image_file(str(path))
    assert opened is not None and opened in OPEN_EDITORS
    assert (opened.view.source.width, opened.view.source.height) == (123, 45)
    assert opened.windowTitle().startswith("fremd.png")
    opened.close()


def test_fullscreen_toggle(qt_app):
    window = EditorWindow(Image.new("RGB", (20, 20), "white"))
    window.show()
    window.toggle_fullscreen()
    assert window.isFullScreen()
    window.toggle_fullscreen()
    assert not window.isFullScreen()
    window.close()
