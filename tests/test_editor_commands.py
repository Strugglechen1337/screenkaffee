from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from screenkaffee.editor import EditorWindow


def command_names(window):
    return [a.text() for a in window.commandbar.actions() if not a.isSeparator()]


def test_command_order_is_stable(qt_app):
    window = EditorWindow(Image.new("RGB", (200, 120), "white"))
    names = command_names(window)
    order = ["Kopieren && Schließen", "Bild exportieren …", "Drucken", "Bildinfo", "Bild kopieren"]
    assert names[:5] == order
    assert names.index("Zuschneiden") < names.index("Ausschneiden") < names.index("Größe / Effekte")
    assert names.count("Drucken") == 1 and names.count("Bildinfo") == 1


def test_copy_and_close_copies_edited_image_and_closes(qt_app, tmp_path):
    window = EditorWindow(Image.new("RGB", (200, 120), "#336699"))
    window.show()
    qt_app.processEvents()
    QApplication.clipboard().clear()
    action = next(a for a in window.commandbar.actions() if a.text() == "Kopieren && Schließen")
    action.trigger()
    qt_app.processEvents()
    image = QApplication.clipboard().image()
    assert (image.width(), image.height()) == (200, 120)
    assert image.pixelColor(10, 10).name() == "#336699"
    from shiboken6 import isValid
    assert not isValid(window) or not window.isVisible()


def test_copy_and_close_saves_project_markup(qt_app, tmp_path):
    project = tmp_path / "bild.screenkaffee.json"
    window = EditorWindow(Image.new("RGB", (200, 120), "white"), project_path=project)
    window.view.add_annotation({"kind": "rectangle", "color": "#ff0000", "width": 3, "points": [10, 10, 80, 60]})
    window.copy_and_close()
    assert project.exists() and "rectangle" in project.read_text(encoding="utf-8")


def no_modal_dialogs(monkeypatch):
    from PySide6.QtWidgets import QDialog
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)


def test_canvas_and_border_commands_change_the_output(qt_app, monkeypatch):
    no_modal_dialogs(monkeypatch)
    window = EditorWindow(Image.new("RGB", (100, 60), "#00ff00"))
    names = command_names(window)
    assert names.index("Größe / Effekte") < names.index("Leinwand") < names.index("Rahmen")
    assert window.view.render_image().size == (100, 60)
    assert window.apply_effect_values({"padding": 10, "background": "#ff0000"})
    padded = window.view.render_image().convert("RGB")
    assert padded.size == (120, 80) and padded.getpixel((2, 2)) == (255, 0, 0) and padded.getpixel((60, 40)) == (0, 255, 0)
    assert window.apply_effect_values({"border_width": 4, "border_color": "#0000ff"})
    assert window.view.render_image().convert("RGB").getpixel((10, 40)) == (0, 0, 255)
    window.view.undo.undo()
    assert window.view.effects["border_width"] == 0
    window.view.undo.undo()
    assert window.view.render_image().size == (100, 60)


def test_canvas_dialog_rejects_invalid_colors(qt_app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a))
    window = EditorWindow(Image.new("RGB", (100, 60), "white"))
    assert not window.apply_effect_values({"background": "kein-farbwert"})
    assert shown and window.view.effects["background"] != "kein-farbwert"


def test_page_keys_request_neighbour_images(qt_app):
    window = EditorWindow(Image.new("RGB", (100, 60), "white"))
    steps = []
    window.navigate.connect(steps.append)
    for name in ("Vorheriges Bild", "Nächstes Bild"):
        next(a for a in window.view_actions if a.text() == name).trigger()
    assert steps == [-1, 1]


def test_main_window_opens_neighbour_image_and_skips_videos(qt_app, tmp_path, monkeypatch):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    workspace = Workspace(tmp_path)
    ids = [workspace.add(Image.new("RGB", (40, 30), color), title=f"Bild {i}") for i, color in enumerate(("red", "green", "blue"))]
    window = MainWindow(workspace)
    order = [window.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(window.list.count())]
    assert set(order) == set(ids)
    assert window.neighbor_image_id(order[0], -1) is None and window.neighbor_image_id(order[-1], 1) is None
    assert window.neighbor_image_id(order[0], 1) == order[1]
    opened = []
    monkeypatch.setattr(window, "open_editor", opened.append)
    editor = EditorWindow(workspace.image(order[0]))
    window.navigate_editor(editor, order[0], 1)
    assert opened == [order[1]]
    window.navigate_editor(EditorWindow(workspace.image(order[-1])), order[-1], 1)
    assert opened == [order[1]]


def test_workspace_thumbnails_fit_several_per_row(qt_app, tmp_path):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    window = MainWindow(Workspace(tmp_path))
    grid = window.list.gridSize()
    assert grid.width() <= 210 and window.list.iconSize().width() <= 180


def test_alt_shift_navigate_deletes_current_image_and_size_label_stays_visible(qt_app, tmp_path, monkeypatch):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    workspace = Workspace(tmp_path)
    ids = [workspace.add(Image.new("RGB", (40, 30), color), title=f"Bild {i}") for i, color in enumerate(("red", "green"))]
    window = MainWindow(workspace)
    order = [window.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(window.list.count())]
    opened = []
    monkeypatch.setattr(window, "open_editor", opened.append)
    editor = EditorWindow(workspace.image(order[0]))
    editor.show()
    qt_app.processEvents()
    editor.statusBar().showMessage("Hinweis in der Statusleiste")
    assert editor.size_label.isVisible() and editor.size_label.text().strip() == "40 × 30"
    assert editor.status_hint.text() == "Hinweis in der Statusleiste"
    window.navigate_editor(editor, order[0], 1, delete_current=True)
    assert workspace.get(order[0]) is None
    assert opened == [order[1]]
