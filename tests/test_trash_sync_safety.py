import json
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtWidgets import QMessageBox
from screenkaffee.workspace import Workspace
from screenkaffee.workspace_sync import sync_workspace


def test_remove_rolls_back_and_partial_rollback_stays_recoverable(tmp_path, monkeypatch):
    w = Workspace(tmp_path)
    ident = w.add(Image.new("RGB", (20, 20), "red"))
    project = w.project_path(ident)
    project.write_text("{}")
    replace = Path.replace
    rollback_fails = False

    def fail(path, target):
        if path == project or (rollback_fails and path.parent.name == "trash"):
            raise PermissionError("Datei gesperrt")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(PermissionError):
        w.remove(ident)
    assert w.get(ident) and w.path(ident).exists()
    assert not w.trash_entries()
    rollback_fails = True
    with pytest.raises(PermissionError):
        w.remove(ident)
    assert w.get(ident) and not w.path(ident).exists()
    assert w.trash_entries()[0]["id"] == ident
    with pytest.raises(ValueError):
        w.remove(ident)
    assert w.trash_entries()[0]["id"] == ident
    assert w.empty_trash() == 0
    assert (w.root / "trash" / (ident + ".png")).exists()
    monkeypatch.setattr(Path, "replace", replace)
    w.restore(ident)
    assert w.path(ident).exists() and project.exists()
    assert not w.trash_entries()
    w.close()


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Dateisperre nur unter Windows")
def test_remove_with_real_windows_file_lock(tmp_path):
    import ctypes
    w = Workspace(tmp_path)
    ident = w.add(Image.new("RGB", (20, 20)))
    project = w.project_path(ident)
    project.write_text("{}")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(project), 0x80000000, 1, None, 3, 0, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        with pytest.raises(PermissionError):
            w.remove(ident)
        assert w.path(ident).exists() and w.get(ident)
        assert not w.trash_entries()
    finally:
        kernel.CloseHandle(handle)
        w.close()


def test_failed_export_preserves_manifest_and_retry(tmp_path, monkeypatch):
    import screenkaffee.workspace_sync as module
    w = Workspace(tmp_path / "a")
    ident = w.add(Image.new("RGB", (20, 20)))
    w.project_path(ident).write_text("{}")
    shared = tmp_path / "shared"
    sync_workspace(w, shared)
    manifest = (shared / "screenkaffee-index.json").read_bytes()
    w.project_path(ident).write_text('{"neu": 1}')
    original = module.copy_file
    calls = 0

    def fail(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError("Abbruch")
        original(source, target)

    monkeypatch.setattr(module, "copy_file", fail)
    with pytest.raises(PermissionError):
        sync_workspace(w, shared)
    assert (shared / "screenkaffee-index.json").read_bytes() == manifest
    monkeypatch.setattr(module, "copy_file", original)
    assert sync_workspace(w, shared)["exported"] == 1
    b = Workspace(tmp_path / "b")
    sync_workspace(b, shared)
    assert b.project_path(ident).read_text() == '{"neu": 1}'
    w.close()
    b.close()


def test_import_removes_obsolete_sidecars_with_backup(tmp_path):
    a, b = Workspace(tmp_path / "a"), Workspace(tmp_path / "b")
    ident = a.add(Image.new("RGB", (20, 20)))
    project = a.project_path(ident)
    project.write_text('{"crop": [0, 0, 5, 5]}')
    shared = tmp_path / "shared"
    sync_workspace(a, shared)
    sync_workspace(b, shared)
    project.unlink()
    sync_workspace(a, shared)
    result = sync_workspace(b, shared)
    assert result == {"imported": 1, "exported": 0, "conflicts": 0}
    assert not b.project_path(ident).exists()
    assert list((b.root / "sync-backups").glob("*/" + project.name))
    assert project.name not in json.loads((shared / "screenkaffee-index.json").read_text())["captures"][ident]["files"]
    a.close()
    b.close()


def test_trash_grid_restore_delete_empty_and_normal_view(qt_app, tmp_path, monkeypatch):
    from screenkaffee.app import MainWindow
    monkeypatch.setattr(MainWindow, "apply_hotkeys", lambda self: {})
    w = Workspace(tmp_path)
    ids = [w.add(Image.new("RGB", (20, 20), "red"), str(i)) for i in range(4)]
    for ident in ids[:3]:
        w.remove(ident)
    window = MainWindow(w)
    assert window.list.count() == 1
    window.collection.setCurrentIndex(window.collection.findData("trash"))
    assert window.list.count() == 3
    assert not window.edit_button.isEnabled()
    for i in range(2):
        window.list.item(i).setSelected(True)
    restored = window.selected_ids(include_trash=True)
    assert window.selected_ids() == []
    window.restore_trash_selected()
    assert all(w.get(i) for i in restored)
    assert window.list.count() == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window.list.item(0).setSelected(True)
    window.delete_trash_selected()
    assert not w.trash_entries() and window.list.count() == 0
    w.remove(ids[3])
    window.refresh_collections()
    window.refresh()
    assert window.list.count() == 1
    window.empty_trash_view()
    assert not w.trash_entries()
    window.collection.setCurrentIndex(0)
    assert window.list.count() == 2 and window.edit_button.isEnabled()
    window.close()


@pytest.mark.parametrize("color", ["#252525", "#ffffff"])
def test_trash_icon_has_visible_glyph(qt_app, color):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap, QPainter
    from screenkaffee.icons import _glyph, round_icon
    pix = QPixmap(44, 44)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    _glyph("trash", painter, color)
    painter.end()
    image = pix.toImage()
    assert sum(image.pixelColor(x, y).alpha() > 0 for x in range(44) for y in range(44)) > 80
    assert not round_icon("trash", color, 16).isNull()


def test_remove_database_failure_rolls_files_back(tmp_path):
    w = Workspace(tmp_path)
    ident = w.add(Image.new("RGB", (20, 20)))
    db = w.db

    class FailedCommit:
        def __getattr__(self, name):
            return getattr(db, name)

        def commit(self):
            raise OSError("Datenbank nicht beschreibbar")

    w.db = FailedCommit()
    with pytest.raises(OSError):
        w.remove(ident)
    assert w.get(ident) and w.path(ident).exists()
    assert not w.trash_entries()
    w.db = db
    w.close()


def test_sync_preserves_favorites_in_both_directions_without_reexport(tmp_path):
    a, b = Workspace(tmp_path / "a"), Workspace(tmp_path / "b")
    shared = tmp_path / "shared"
    ident = a.add(Image.new("RGB", (20, 20)))
    a.db.execute("UPDATE captures SET favorite=1 WHERE id=?", (ident,))
    a.db.commit()
    sync_workspace(a, shared)
    assert sync_workspace(b, shared) == {"imported": 1, "exported": 0, "conflicts": 0}
    assert b.get(ident)["favorite"] == 1
    b.update(ident, "Neuer Titel", "", "Alle Aufnahmen")
    sync_workspace(b, shared)
    assert sync_workspace(a, shared) == {"imported": 1, "exported": 0, "conflicts": 0}
    assert a.get(ident)["favorite"] == 1
    assert sync_workspace(b, shared) == {"imported": 0, "exported": 0, "conflicts": 0}
    a.close()
    b.close()
