import sqlite3
import pytest
from PIL import Image
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDropEvent
from screenkaffee.workspace import Workspace


@pytest.fixture
def workspace(tmp_path):
    ws = Workspace(tmp_path)
    yield ws
    ws.close()


def add(ws, title, collection="Alle Aufnahmen"):
    return ws.add(Image.new("RGB", (20, 10), "red"), title=title, collection=collection)


def test_rename_updates_the_captures(workspace):
    ident = add(workspace, "A", "Privat")
    workspace.rename_collection("Privat", "Familie")
    assert workspace.get(ident)["collection"] == "Familie"
    assert "Privat" not in workspace.collections() and "Familie" in workspace.collections()


def test_rename_rejects_clashes_and_the_default_collection(workspace):
    workspace.create_collection("Eins")
    workspace.create_collection("Zwei")
    for old, new in (("Eins", "Zwei"), ("Eins", "Alle Aufnahmen"), ("Eins", "  "), ("Alle Aufnahmen", "X"), ("Gibt es nicht", "Y")):
        with pytest.raises(ValueError):
            workspace.rename_collection(old, new)


def test_delete_keeps_the_captures_or_trashes_them(workspace):
    kept = add(workspace, "Bleibt", "Alt")
    assert workspace.delete_collection("Alt") == 1
    assert workspace.get(kept)["collection"] == "Alle Aufnahmen" and "Alt" not in workspace.collections()
    gone = add(workspace, "Weg", "Temp")
    workspace.delete_collection("Temp", delete_files=True)
    assert workspace.get(gone) is None and any(r["id"] == gone for r in workspace.trash_entries())
    with pytest.raises(ValueError):
        workspace.delete_collection("Alle Aufnahmen")


def test_moving_captures_creates_the_target_collection(workspace):
    a, b = add(workspace, "A"), add(workspace, "B")
    workspace.move_to_collection([a, b], "Neu")
    assert {workspace.get(i)["collection"] for i in (a, b)} == {"Neu"} and "Neu" in workspace.collections()
    workspace.move_to_collection([a], "Alle Aufnahmen")
    assert workspace.get(a)["collection"] == "Alle Aufnahmen"
    with pytest.raises(KeyError):
        workspace.move_to_collection(["x" * 32], "Neu")


def test_collection_order_can_be_changed_and_persists(workspace, tmp_path):
    for name in ("Bravo", "Alpha", "Charlie"):
        workspace.create_collection(name)
    assert workspace.collections() == ["Alpha", "Bravo", "Charlie"]
    assert workspace.move_collection("Charlie", -1) and workspace.collections() == ["Alpha", "Charlie", "Bravo"]
    assert workspace.move_collection("Alpha", 1) and workspace.collections() == ["Charlie", "Alpha", "Bravo"]
    assert not workspace.move_collection("Bravo", 1) and not workspace.move_collection("Charlie", -1)
    again = Workspace(tmp_path)
    assert again.collections() == ["Charlie", "Alpha", "Bravo"]
    again.close()


def test_collection_icons_are_stored(workspace):
    workspace.create_collection("Video")
    assert workspace.collection_icon("Video") == ""
    workspace.set_collection_icon("Video", "video")
    assert workspace.collection_icon("Video") == "video"
    with pytest.raises(ValueError):
        workspace.set_collection_icon("Alle Aufnahmen", "video")


def test_old_databases_are_migrated(tmp_path):
    db = sqlite3.connect(tmp_path / "workspace.sqlite")
    db.execute("CREATE TABLE captures (id TEXT PRIMARY KEY, title TEXT NOT NULL, created TEXT NOT NULL, filename TEXT NOT NULL, "
               "width INTEGER, height INTEGER, tags TEXT DEFAULT '', collection TEXT DEFAULT 'Alle Aufnahmen', metadata TEXT NOT NULL)")
    db.execute("CREATE TABLE collections(name TEXT PRIMARY KEY)")
    db.execute("INSERT INTO collections VALUES ('Alt')")
    db.commit()
    db.close()
    ws = Workspace(tmp_path)
    assert ws.collections() == ["Alt"]
    ws.rename_collection("Alt", "Neu")
    assert ws.collections() == ["Neu"]
    ws.close()


def test_dropping_captures_on_a_sidebar_entry_moves_them(qt_app, tmp_path):
    from screenkaffee.app import MainWindow, MEDIA_MIME
    ws = Workspace(tmp_path)
    ident = add(ws, "Bild")
    ws.create_collection("Ziel")
    window = MainWindow(ws)
    window.show()
    qt_app.processEvents()
    row = next(i for i in range(window.collection_list.count()) if window.collection_list.item(i).text() == "Ziel")
    position = window.collection_list.visualItemRect(window.collection_list.item(row)).center()
    mime = QMimeData()
    mime.setData(MEDIA_MIME, ident.encode())
    event = QDropEvent(QPointF(position), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.collection_list.dropEvent(event)
    assert ws.get(ident)["collection"] == "Ziel"


def test_sidebar_actions_rename_reorder_and_delete(qt_app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    from screenkaffee.app import MainWindow
    ws = Workspace(tmp_path)
    for name in ("Eins", "Zwei"):
        ws.create_collection(name)
    window = MainWindow(ws)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Erste", True))
    window.rename_collection("Eins")
    assert ws.collections() == ["Erste", "Zwei"]
    window.shift_collection("Zwei", -1)
    assert ws.collections() == ["Zwei", "Erste"]
    assert [window.collection_list.item(i).text() for i in range(window.collection_list.count())][:3] == ["Alle Aufnahmen", "Zwei", "Erste"]
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: self.buttons()[0])
    window.delete_collection("Zwei")
    assert ws.collections() == ["Erste"]


def test_single_image_export_can_write_pdf_and_word(tmp_path):
    from screenkaffee.batch import export_any
    image = Image.new("RGB", (120, 80), "blue")
    pdf = export_any(image, "Titel", tmp_path / "a.pdf")
    docx = export_any(image, "Titel", tmp_path / "a.docx")
    png = export_any(image, "Titel", tmp_path / "a.png")
    assert pdf.read_bytes()[:5] == b"%PDF-" and docx.read_bytes()[:2] == b"PK" and png.read_bytes()[:4] == b"\x89PNG"


def test_project_bundle_export_import_and_f10_sidebar_toggle(qt_app, tmp_path):
    import json
    from PySide6.QtTest import QTest
    from screenkaffee.app import MainWindow
    ws1 = Workspace(tmp_path / "ws1")
    ident = add(ws1, "Projektbild")
    ws1.project_path(ident).write_text(json.dumps({
        "version": 1, "crop": [0, 0, 20, 10], "effects": {},
        "annotations": [{"kind": "rectangle", "color": "#00ff00", "width": 2, "points": [1, 1, 10, 8]}]
    }), encoding="utf-8")
    win1 = MainWindow(ws1)
    win1.show()
    qt_app.processEvents()
    bundle_path = win1.export_project_bundle(ident, tmp_path / "exports")
    assert bundle_path.exists() and bundle_path.name.endswith(".screenkaffee.json")

    assert win1.collection_list.isVisible()
    win1.toggle_collections_panel(False)
    assert not win1.collection_list.isVisible()
    win1.toggle_collections_panel(True)
    assert win1.collection_list.isVisible()

    QTest.keyClicks(win1.list, "Projekt")
    assert win1.search.text() == "Projekt" and win1.list.count() == 1
    QTest.keyClick(win1.list, Qt.Key.Key_Escape)
    assert win1.search.text() == ""

    ws2 = Workspace(tmp_path / "ws2")
    win2 = MainWindow(ws2)
    win2.import_paths([str(bundle_path)])
    rows = ws2.rows()
    assert len(rows) == 1 and rows[0]["title"] == "Projektbild"
    assert ws2.project_path(rows[0]["id"]).exists()
    ws1.close()
    ws2.close()
