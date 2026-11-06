"""Ebenen, Sperren, Ausrichten/Verteilen, Pfeiltasten, Auto-Trim und Lupen-Verbindungslinien im Editor."""
import json

from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from screenkaffee import capture_options as co
from screenkaffee.editor import EditorView, EditorWindow
from screenkaffee.effects import trim_bounds


def rect(view, x, y, w=20, h=20, color="#ff0000"):
    return view.add_annotation({"kind": "rectangle", "color": color, "width": 2,
                                "points": [x, y, x + w, y + h], "fill": color})


def select(view, *items):
    view.scene().clearSelection()
    for item in items:
        item.setSelected(True)


def scene_rect(item):
    return item.mapRectToScene(item.body_rect())


def test_layer_order_commands_with_undo_and_project_roundtrip(qt_app):
    view = EditorView(Image.new("RGB", (200, 150), "white"))
    a, b, c = rect(view, 10, 10), rect(view, 20, 20), rect(view, 30, 30)
    assert view.annotations() == [a, b, c]
    select(view, a)
    assert view.reorder_selected("forward")
    assert view.annotations() == [b, a, c]
    assert view.reorder_selected("front")
    assert view.annotations() == [b, c, a]
    assert not view.reorder_selected("front")              # schon ganz oben: kein Undo-Schritt
    view.undo.undo()
    assert view.annotations() == [b, a, c]
    view.undo.redo()
    select(view, c)
    assert view.reorder_selected("back")
    assert view.annotations() == [c, b, a]
    select(view, a)
    assert view.reorder_selected("backward")
    assert view.annotations() == [c, a, b]
    # Reihenfolge übersteht Speichern/Laden (JSON) und bestimmt den Export
    project = json.loads(json.dumps(view.project()))
    other = EditorView(Image.new("RGB", (200, 150), "white"))
    other.load_project(project)
    assert [x.data["points"][0] for x in other.annotations()] == [30, 10, 20]


def test_layer_order_is_visible_in_export(qt_app):
    view = EditorView(Image.new("RGB", (100, 100), "white"))
    red = rect(view, 10, 10, 50, 50, "#ff0000")
    blue = rect(view, 30, 30, 50, 50, "#0000ff")
    assert view.render_image().getpixel((45, 45))[2] > 200          # blau liegt oben
    select(view, red)
    view.reorder_selected("front")
    assert view.render_image().getpixel((45, 45))[0] > 200          # jetzt rot oben
    assert blue.zValue() < red.zValue()


def test_lock_prevents_dragging_and_survives_save_load(qt_app):
    window = EditorWindow(Image.new("RGB", (400, 300), "white"))
    window.resize(900, 700)
    window.show()
    qt_app.processEvents()
    view = window.view
    item = rect(view, 50, 50, 60, 60)
    select(view, item)
    assert view.toggle_lock()
    assert item.locked() and not item.flags() & item.GraphicsItemFlag.ItemIsMovable
    start = view.mapFromScene(QPointF(80, 80))
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(view.viewport(), start + type(start)(60, 40))
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + type(start)(60, 40))
    qt_app.processEvents()
    assert item.pos() == QPointF(0, 0)
    select(view, item)
    assert not view.nudge_selected(5, 0) and item.pos() == QPointF(0, 0)   # Pfeiltasten auch gesperrt
    project = json.loads(json.dumps(view.project()))
    assert project["annotations"][0]["locked"] is True
    other = EditorView(Image.new("RGB", (400, 300), "white"))
    other.load_project(project)
    loaded = other.annotations()[0]
    assert loaded.locked() and not loaded.flags() & loaded.GraphicsItemFlag.ItemIsMovable
    view.undo.undo()                                                     # Sperren rückgängig
    assert not item.locked() and item.flags() & item.GraphicsItemFlag.ItemIsMovable
    view.undo.redo()
    assert item.locked()
    select(view, item)
    view.toggle_lock()                                                   # erneut: entsperren
    assert not item.locked() and "locked" not in item.serialize()


def test_context_menu_entries_for_layers_alignment_and_lock(qt_app):
    from PySide6.QtWidgets import QMenu
    window = EditorWindow(Image.new("RGB", (300, 200), "white"))
    view = window.view
    item = rect(view, 10, 10)
    select(view, item)
    menu = QMenu()
    view.fill_arrange_menu(menu)
    texts = [a.text() for a in menu.actions()]
    assert "Anordnen (Ebenen)" in texts and "Ausrichten / Verteilen" in texts
    assert "Sperren (nicht verschiebbar)" in texts
    order = [a.text().split("\t")[0] for a in menu.actions()[0].menu().actions()]
    assert order == ["Ganz nach vorne", "Nach vorne", "Nach hinten", "Ganz nach hinten"]
    view.toggle_lock()
    window.fill_arrange_menu()
    assert "Entsperren" in [a.text() for a in window.arrange_menu.actions()]


def test_align_and_distribute_are_single_undo_steps(qt_app):
    view = EditorView(Image.new("RGB", (400, 300), "white"))
    a, b, c = rect(view, 10, 10, 20, 20), rect(view, 100, 50, 40, 30), rect(view, 300, 120, 30, 10)
    select(view, a, b, c)
    before = [item.pos() for item in (a, b, c)]
    assert view.align_selected("left")
    assert {round(scene_rect(x).left()) for x in (a, b, c)} == {10}
    view.undo.undo()
    assert [item.pos() for item in (a, b, c)] == before
    view.undo.redo()
    view.undo.undo()
    for mode, getter, expected in (("right", lambda r: r.right(), 330), ("top", lambda r: r.top(), 10),
                                   ("bottom", lambda r: r.bottom(), 130)):
        assert view.align_selected(mode)
        assert {round(getter(scene_rect(x))) for x in (a, b, c)} == {expected}
        view.undo.undo()
    assert view.align_selected("hcenter")
    assert {round(scene_rect(x).center().x()) for x in (a, b, c)} == {170}
    view.undo.undo()
    assert view.align_selected("vcenter")
    assert {round(scene_rect(x).center().y()) for x in (a, b, c)} == {70}
    view.undo.undo()
    assert [item.pos() for item in (a, b, c)] == before
    assert view.distribute_selected("x")
    left, mid, right = (scene_rect(x) for x in (a, b, c))
    assert round(mid.left() - left.right(), 6) == round(right.left() - mid.right(), 6)
    assert round(left.left()) == 10 and round(right.right()) == 330      # äußere bleiben stehen
    view.undo.undo()
    assert view.distribute_selected("y")
    top, mid, bottom = sorted((scene_rect(x) for x in (a, b, c)), key=lambda r: r.center().y())
    assert round(mid.top() - top.bottom(), 6) == round(bottom.top() - mid.bottom(), 6)
    view.undo.undo()
    assert [item.pos() for item in (a, b, c)] == before
    select(view, a)
    assert not view.align_selected("left")            # eine Markierung allein: nichts zu tun
    select(view, a, b)
    assert not view.distribute_selected("x")          # Verteilen braucht drei


def test_locked_items_are_skipped_when_aligning(qt_app):
    view = EditorView(Image.new("RGB", (400, 300), "white"))
    a, b, c = rect(view, 10, 10), rect(view, 100, 50), rect(view, 200, 80)
    select(view, c)
    view.toggle_lock()
    select(view, a, b, c)
    view.align_selected("top")
    assert c.pos() == QPointF(0, 0) and round(scene_rect(b).top()) == 10


def test_arrow_keys_nudge_selection_and_merge_into_one_undo_step(qt_app):
    window = EditorWindow(Image.new("RGB", (300, 200), "white"))
    window.show()
    qt_app.processEvents()
    view = window.view
    a, b = rect(view, 10, 10), rect(view, 50, 50)
    select(view, a, b)
    view.setFocus()
    count = view.undo.count()
    QTest.keyClick(view, Qt.Key.Key_Right)
    QTest.keyClick(view, Qt.Key.Key_Right)
    QTest.keyClick(view, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    assert a.pos() == QPointF(2, 10) and b.pos() == QPointF(2, 10)
    assert view.undo.count() == count + 1                       # zusammengefasst
    view.undo.undo()
    assert a.pos() == QPointF(0, 0) and b.pos() == QPointF(0, 0)
    view.undo.redo()
    assert a.pos() == QPointF(2, 10)
    select(view, a)                                              # andere Auswahl: neuer Undo-Schritt
    QTest.keyClick(view, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    assert a.pos() == QPointF(-8, 10) and b.pos() == QPointF(2, 10)
    assert view.undo.count() == count + 2


def test_polygon_enter_and_escape_still_work_with_arrow_keys(qt_app):
    window = EditorWindow(Image.new("RGB", (300, 200), "white"))
    window.show()
    qt_app.processEvents()
    view = window.view
    view.set_tool("polygon")
    view.polygon_points = [[10, 10], [100, 20], [60, 120]]
    view.setFocus()
    QTest.keyClick(view, Qt.Key.Key_Left)                       # kein Absturz, keine Verschiebung
    QTest.keyClick(view, Qt.Key.Key_Return)
    assert len(view.annotations()) == 1 and view.annotations()[0].data["kind"] == "polygon"
    view.polygon_points = [[10, 10], [50, 50]]
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert view.tool == "select" and view.polygon_points == []


def test_trim_bounds_detects_uniform_and_transparent_borders():
    image = Image.new("RGB", (100, 80), (250, 250, 250))
    image.paste((10, 20, 30), (20, 15, 70, 60))
    assert trim_bounds(image) == (20, 15, 70, 60)
    noisy = image.copy()
    noisy.putpixel((2, 2), (244, 247, 252))                      # innerhalb der Toleranz
    assert trim_bounds(noisy, tolerance=8) == (20, 15, 70, 60)
    assert trim_bounds(noisy, tolerance=2) == (2, 2, 70, 60)
    transparent = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
    transparent.paste((255, 0, 0, 255), (5, 10, 25, 30))
    assert trim_bounds(transparent) == (5, 10, 25, 30)
    assert trim_bounds(Image.new("RGB", (30, 30), "white")) is None   # einfarbig: nichts zu tun
    full = Image.new("RGB", (30, 30), "white")
    full.putpixel((0, 0), (0, 0, 0))
    full.putpixel((29, 29), (0, 0, 0))
    assert trim_bounds(full) is None                                  # kein Rand


def test_editor_auto_trim_is_an_undoable_crop(qt_app):
    image = Image.new("RGB", (200, 120), "white")
    image.paste((0, 0, 255), (30, 20, 150, 90))
    window = EditorWindow(image)
    view = window.view
    window.auto_trim_borders()
    assert view.crop_rect.getRect() == (30, 20, 120, 70)
    assert view.render_image().size == (120, 70)
    assert not view.auto_trim()                                   # zweiter Aufruf: nichts mehr
    view.undo.undo()
    assert view.crop_rect.getRect() == (0, 0, 200, 120)
    view.undo.redo()
    project = json.loads(json.dumps(view.project()))
    assert project["crop"] == [30, 20, 120, 70]
    labels = [a.text() for m in window.findChildren(type(window.arrange_menu)) for a in m.actions()]
    assert "Ränder automatisch zuschneiden" in labels


def test_auto_trim_capture_option_defaults_off():
    image = Image.new("RGB", (50, 40), "white")
    image.paste((255, 0, 0), (10, 10, 30, 20))
    assert co.DEFAULTS["auto_trim"] is False
    assert co.post_process(image, co.DEFAULTS).size == (50, 40)
    assert co.post_process(image, dict(co.DEFAULTS, auto_trim=True)).size == (20, 10)
    assert co.validate(dict(co.DEFAULTS, auto_trim=True)) == {}


def magnifier_view(lines):
    image = Image.new("RGB", (300, 200), "white")
    image.paste((0, 0, 0), (40, 40, 60, 60))
    view = EditorView(image)
    item = view.add_annotation({"kind": "magnifier", "color": "#00c000", "width": 3,
                                "points": [180, 60, 260, 140], "zoom": 2,
                                "sample_box": [30, 30, 70, 70], **({"magnifier_lines": True} if lines else {})})
    return view, item


def is_green(pixel):
    return pixel[1] > 120 and pixel[0] < 100 and pixel[2] < 100


def test_magnifier_connection_lines_are_exported(qt_app):
    view, item = magnifier_view(lines=True)
    rendered = view.render_image()
    border = [rendered.getpixel((x, 30)) for x in range(35, 66)]    # obere Kante des Quellrahmens
    assert sum(is_green(p) for p in border) > 20
    between = [rendered.getpixel((x, y)) for x in range(75, 175) for y in range(20, 160)]
    assert sum(is_green(p) for p in between) > 80                    # Verbindungslinien zwischen Quelle und Glas
    plain, _ = magnifier_view(lines=False)
    without = plain.render_image()
    assert not any(is_green(without.getpixel((x, 30))) for x in range(35, 66))
    assert not any(is_green(without.getpixel((x, y))) for x in range(75, 175) for y in range(20, 160))


def test_magnifier_lines_roundtrip_and_undo(qt_app):
    view, item = magnifier_view(lines=False)
    from screenkaffee.editor import DataCommand
    before = item.serialize()
    view.undo.push(DataCommand(item, before, dict(before, magnifier_lines=True)))
    assert item.magnifier_link() is not None
    project = json.loads(json.dumps(view.project()))
    assert project["annotations"][0]["magnifier_lines"] is True
    other = EditorView(Image.new("RGB", (300, 200), "white"))
    other.load_project(project)
    assert other.annotations()[0].magnifier_link() is not None
    view.undo.undo()
    assert item.magnifier_link() is None
    # Das Glas bleibt allein anklickbar: der Quellbereich gehört nicht zur Trefferfläche
    view.undo.redo()
    assert not item.shape().contains(QPointF(40, 40))
    assert item.boundingRect().contains(QPointF(40, 40))
