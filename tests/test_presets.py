from screenkaffee.presets import load_presets, save_preset, delete_preset


def test_preset_roundtrip(tmp_path):
    f = tmp_path / "p.json"
    save_preset("Rot dick", {"tool": "arrow", "color": "#ff0000", "width": 9, "font_size": 30, "x": 1}, f)
    assert load_presets(f) == {"Rot dick": {"tool": "arrow", "color": "#ff0000", "width": 9, "font_size": 30}}
    assert delete_preset("Rot dick", f) == {}
    assert load_presets(tmp_path / "missing.json") == {}


def test_apply_preset_in_editor(qt_app):
    from PIL import Image
    from screenkaffee.editor import EditorWindow
    w = EditorWindow(Image.new("RGB", (50, 50), "white"))
    w.apply_preset({"tool": "arrow", "color": "#00ff00", "width": 7, "font_size": 33})
    assert (w.view.tool, w.view.color, w.view.width, w.view.font_size) == ("arrow", "#00ff00", 7, 33)
