import json
from PIL import Image
from PySide6.QtCore import QRectF, QPointF
from screenkaffee.editor import EditorView, CropCommand, MoveCommand
from screenkaffee.workspace import Workspace


def test_redaction_export_and_undo_survive_project_reload(qt_app):
    original = Image.new("RGB", (200, 100), "white")
    view = EditorView(original)
    item = view.add_annotation({"kind": "redact", "color": "#ff0000", "width": 4,
                                "points": [10, 10, 80, 60]})
    assert view.render_image().getpixel((20, 20)) == (17, 17, 17)
    view.undo.undo()
    assert view.render_image().getpixel((20, 20)) == (255, 255, 255)
    view.undo.redo()
    view.undo.push(MoveCommand(item, QPointF(0, 0), QPointF(10, 0)))
    view.undo.push(CropCommand(view, QRectF(20, 10, 100, 70)))
    exported = view.render_image()
    assert exported.size == (100, 70)
    assert exported.getpixel((5, 5)) == (17, 17, 17)
    reloaded = EditorView(original)
    reloaded.load_project(json.loads(json.dumps(view.project())))
    assert reloaded.render_image().tobytes() == exported.tobytes()
    assert original.getpixel((20, 20)) == (255, 255, 255)


def test_workspace_persistence_search_and_recoverable_delete(tmp_path):
    workspace = Workspace(tmp_path)
    ident = workspace.add(Image.new("RGB", (32, 20), "red"), "Fenster 100%", {"hdr": True})
    workspace.update(ident, "Fenster 100%", "HDR Arbeit", "Tests")
    assert len(workspace.rows("100%")) == 1
    assert len(workspace.rows("HDR", "Tests")) == 1
    assert not workspace.rows("HDR", "Andere")
    project = workspace.path(ident).with_suffix(".prism.json")
    project.write_text('{}')
    workspace.close()
    workspace = Workspace(tmp_path)
    assert workspace.image(ident).size == (32, 20)
    workspace.remove(ident)
    assert not workspace.rows()
    assert (tmp_path / "trash" / (ident + ".png")).exists()
    assert (tmp_path / "trash" / (ident + ".prism.json")).exists()
    workspace.close()


def test_annotation_layer_order_survives_deletion_and_reload(qt_app):
    view = EditorView(Image.new("RGB", (100, 100), "white"))
    first = view.add_annotation({"kind": "rectangle", "color": "#ff0000", "width": 2, "points": [0, 0, 99, 99]})
    view.add_annotation({"kind": "highlight", "color": "#ff0000", "width": 2, "points": [10, 10, 80, 80]})
    from screenkaffee.editor import DeleteCommand
    view.undo.push(DeleteCommand(view, [first]))
    view.add_annotation({"kind": "redact", "color": "#000000", "width": 2, "points": [10, 10, 80, 80]})
    before = view.render_image()
    reloaded = EditorView(view.source)
    reloaded.load_project(view.project())
    assert before.getpixel((40, 40)) == (17, 17, 17)
    assert reloaded.render_image().tobytes() == before.tobytes()
