from PIL import Image
from screenkaffee.editor import EditorView, line_geometry


def test_line_geometry():
    assert line_geometry([0, 0, 100, 0])[2] is None
    c = line_geometry([0, 0, 100, 0], 20)[2]
    assert round(c.x()) == 50 and round(c.y()) == 40


def render(**extra):
    v = EditorView(Image.new("RGB", (200, 120), "white"))
    v.add_annotation(dict({"kind": "arrow", "color": "#ff0000", "width": 4, "points": [20, 60, 180, 60]}, **extra))
    return v.render_image().convert("RGB")


def test_arrow_style_changes_pixels(qt_app):
    base = render()
    assert render(arrow_style="open").tobytes() != base.tobytes()
    assert render(arrow_style="double").getpixel((22, 60)) != (255, 255, 255)
    assert render(curve=30).tobytes() != base.tobytes()
    assert render(line_style="dash").tobytes() != base.tobytes()
    assert render(curve=30, arrow_style="double").size == (200, 120)


def test_image_watermark_position(tmp_path):
    from screenkaffee.effects import apply_effects
    mark = tmp_path / "m.png"
    Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(mark)
    base = Image.new("RGB", (100, 100), "white")
    for pos, probe in [("top-left", (14, 14)), ("bottom-right", (85, 85)), ("middle-center", (50, 50))]:
        out = apply_effects(base, {"watermark_image": str(mark), "watermark_position": pos, "watermark_scale": 20, "watermark_opacity": 255}).convert("RGB")
        assert out.getpixel(probe) == (255, 0, 0), pos


def test_step_connector(qt_app):
    v = EditorView(Image.new("RGB", (300, 200), "white"))
    base = {"kind": "step", "color": "#0000ff", "width": 4}
    v.add_annotation(dict(base, points=[20, 20, 0, 0], text="1", text_width=46, text_height=46))
    v.add_annotation(dict(base, points=[220, 20, 0, 0], text="2", text_width=46, text_height=46, connect=True))
    px = v.render_image().convert("RGB").getpixel((120, 39))
    assert px != (255, 255, 255)


def test_step_label_formats():
    from screenkaffee.editor import step_label
    assert [step_label(n) for n in (1, 12)] == ["1", "12"]
    assert [step_label(n, "A") for n in (1, 26, 27, 52, 53)] == ["A", "Z", "AA", "AZ", "BA"]
    assert [step_label(n, "I") for n in (1, 4, 9, 14, 1994)] == ["I", "IV", "IX", "XIV", "MCMXCIV"]
    assert step_label(5000, "I") == "5000"


def test_step_connector_with_letter_format(qt_app):
    v = EditorView(Image.new("RGB", (300, 200), "white"))
    base = {"kind": "step", "color": "#0000ff", "width": 4, "text_width": 46, "text_height": 46, "step_format": "A"}
    v.add_annotation(dict(base, points=[20, 20, 0, 0], text="A", number=1))
    second = dict(base, points=[220, 20, 0, 0], text="B", number=2, connect=True)
    v.add_annotation(second)
    assert v.render_image().convert("RGB").getpixel((120, 39)) != (255, 255, 255)
    assert [i.data["text"] for i in v.annotations()] == ["A", "B"]


def test_keep_aspect():
    from screenkaffee.editor import keep_aspect
    assert keep_aspect(0, 0, 100, 50, 200, 60) == (200, 100)
    assert keep_aspect(0, 0, 100, 50, 120, 100) == (200, 100)
