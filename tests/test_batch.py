from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from screenkaffee.batch import (export_batch, recompress_png, renumber_titles, safe_name, split_title_extension,
                                unique_path)


def test_safe_names_and_unique_paths(tmp_path):
    assert safe_name('a/b:c*"d"') == "a_b_c_d_"
    assert safe_name("   ", "x") == "x"
    (tmp_path / "bild.png").write_bytes(b"1")
    assert unique_path(tmp_path, "bild", ".png").name == "bild_2.png"


def test_title_extension_is_detected():
    assert split_title_extension("Foto.JPG") == ("Foto", ".jpg")
    assert split_title_extension("Foto.jpeg") == ("Foto", ".jpg")
    assert split_title_extension("Version 2.3") == ("Version 2.3", None)
    assert split_title_extension(".png") == (".png", None)


def test_batch_export_resizes_adds_effects_and_never_overwrites(tmp_path):
    items = [("Eins", Image.new("RGB", (200, 100), "red")), ("Eins", Image.new("RGB", (200, 100), "blue"))]
    paths = export_batch(items, tmp_path / "aus", ".jpg", {"scale_percent": 50, "border_width": 4, "border_color": "#00ff00"})
    assert [p.name for p in paths] == ["Eins.jpg", "Eins_2.jpg"]
    with Image.open(paths[0]) as image:
        assert image.size == (100, 50)
        assert image.getpixel((1, 25))[1] > 150
    again = export_batch(items[:1], tmp_path / "aus", ".jpg")
    assert again[0].name == "Eins_3.jpg"


def test_batch_export_rejects_unknown_formats(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        export_batch([("x", Image.new("RGB", (4, 4)))], tmp_path, ".xyz")


def test_renumber_titles_follow_capture_order():
    rows = [{"id": "b", "created": "2026-10-28T10:00:00"}, {"id": "a", "created": "2026-10-27T10:00:00"},
            {"id": "c", "created": "2026-10-29T10:00:00"}]
    assert renumber_titles(rows) == [("a", "screenshot_1"), ("b", "screenshot_2"), ("c", "screenshot_3")]
    assert renumber_titles(rows[:1], "Test/Ordner", start=5) == [("b", "Test_Ordner_5")]


def test_recompress_is_lossless_and_keeps_metadata(tmp_path):
    from screenkaffee.color import save_png
    image = Image.new("RGB", (300, 200), "white")
    for x in range(0, 300, 7):
        image.putpixel((x, x % 200), (x % 255, 10, 200))
    path = tmp_path / "a.png"
    save_png(image, path, {"Software": "screenkaffee"})
    before = path.stat().st_size
    saved = recompress_png(path)
    assert saved >= 0 and path.stat().st_size == before - saved
    with Image.open(path) as result:
        assert result.convert("RGB").tobytes() == image.tobytes()
        assert result.text["Software"] == "screenkaffee" and result.info.get("icc_profile")
    assert not list(tmp_path.glob("*.tmp"))
    assert recompress_png(path) == 0


def test_quick_view_closes_with_space_and_right_click(qt_app):
    from shiboken6 import isValid
    from screenkaffee.quickview import QuickView
    pixmap = QPixmap(120, 80)
    pixmap.fill(Qt.GlobalColor.blue)
    view = QuickView(pixmap)
    view.show()
    assert view.windowFlags() & Qt.WindowType.WindowStaysOnTopHint and view.size().width() == 120
    QTest.keyClick(view, Qt.Key.Key_Space)
    qt_app.processEvents()
    assert not isValid(view) or not view.isVisible()


def test_workspace_menu_offers_batch_functions_and_space_opens_quick_view(qt_app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMenu
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    workspace = Workspace(tmp_path)
    workspace.add(Image.new("RGB", (40, 30), "red"), title="Eins")
    window = MainWindow(workspace)
    labels = [a.text() for menu in window.findChildren(QMenu) for a in menu.actions()]
    for text in ("Stapelexport mit Größe und Effekten …", "Neu komprimieren (verlustfrei)", "Schnellansicht (Leertaste)"):
        assert text in labels
    window.list.setCurrentRow(0)
    window.list.item(0).setSelected(True)
    opened = []
    monkeypatch.setattr(window, "quick_view_selected", lambda: opened.append(True))
    QTest.keyClick(window.list, Qt.Key.Key_Space)
    assert opened == [True]
