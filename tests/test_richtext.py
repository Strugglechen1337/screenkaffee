from PIL import Image
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from screenkaffee.editor import EditorView
from screenkaffee.richtext import RichTextDialog, clean_spans


def render(**extra):
    view = EditorView(Image.new("RGB", (320, 120), "white"))
    data = {"kind": "text", "color": "#000000", "width": 4, "points": [10, 10, 310, 110], "text": "Hallo Welt",
            "text_width": 300, "text_height": 100, "font_size": 30, "bold": False}
    view.add_annotation(dict(data, **extra))
    return view.render_image().convert("RGB")


def count(image, predicate):
    pixels = image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata()
    return sum(1 for pixel in pixels if predicate(pixel))


def test_spans_change_only_the_marked_part(qt_app):
    plain = render()
    red_tail = render(spans=[{"start": 6, "end": 10, "color": "#ff0000"}])
    assert plain.tobytes() != red_tail.tobytes()
    assert count(plain, lambda p: p[0] > 200 and p[1] < 60) == 0
    assert count(red_tail, lambda p: p[0] > 200 and p[1] < 60) > 20
    red_head = render(spans=[{"start": 0, "end": 5, "color": "#ff0000"}])

    assert red_head.tobytes() != red_tail.tobytes()
    assert count(red_head, lambda p: p[0] > 200 and p[1] < 60) > 20


def test_underline_and_size_spans_render(qt_app):
    plain = render()
    for span in ({"underline": True}, {"size": 50}):
        assert render(spans=[{"start": 0, "end": 5, **span}]).tobytes() != plain.tobytes(), span


def test_bold_and_italic_spans_reach_the_text_document(qt_app):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from screenkaffee.richtext import build_document, base_font
    data = {"kind": "text", "color": "#000000", "text": "Hallo Welt", "font_size": 22, "bold": False,
            "spans": [{"start": 0, "end": 5, "bold": True}, {"start": 6, "end": 10, "italic": True}]}
    document = build_document(data, base_font(data), 200, Qt.AlignmentFlag.AlignLeft)
    formats = [fragment.charFormat() for fragment in (document.begin().begin().fragment(),)]
    assert formats[0].fontWeight() >= QFont.Weight.Bold and not formats[0].fontItalic()
    cursor = document.find("Welt")
    assert cursor.charFormat().fontItalic() and cursor.charFormat().fontWeight() < QFont.Weight.Bold


def test_glow_works_with_spans(qt_app):
    spans = [{"start": 0, "end": 5, "color": "#222222"}]
    assert render(spans=spans, glow_radius=6, glow_color="#00ff00").tobytes() != render(spans=spans).tobytes()


def test_invalid_spans_are_ignored(qt_app):
    assert clean_spans([{"start": 5, "end": 2, "bold": True}, {"start": "x"}, {"start": 0, "end": 3},
                        {"start": -4, "end": 99, "bold": True}], 10) == [{"start": 0, "end": 10, "bold": True}]
    assert render(spans=[{"start": 20, "end": 40, "bold": True}]).tobytes() == render().tobytes()


def test_dialog_reads_back_formatting_and_spans_survive_serialization(qt_app):
    data = {"kind": "text", "color": "#000000", "text": "Hallo Welt", "font_size": 22, "bold": False,
            "spans": [{"start": 0, "end": 5, "bold": True}, {"start": 6, "end": 10, "color": "#ff0000", "italic": True}]}
    dialog = RichTextDialog(None, data)
    text, spans = dialog.result_values()
    assert text == "Hallo Welt"
    assert spans == data["spans"]
    cursor = dialog.editor.textCursor()
    cursor.setPosition(5)
    cursor.setPosition(6, QTextCursor.MoveMode.KeepAnchor)
    dialog.editor.setTextCursor(cursor)
    dialog.merge(underline=True)
    _, spans = dialog.result_values()
    assert {"start": 5, "end": 6, "underline": True} in spans
    view = EditorView(Image.new("RGB", (100, 100), "white"))
    view.add_annotation(dict(data, points=[0, 0, 90, 90], text_width=90, text_height=90))
    assert view.annotations()[0].serialize()["spans"] == data["spans"]


def test_multiline_offsets_match_plain_text(qt_app):
    data = {"kind": "text", "color": "#000000", "text": "eins\nzwei", "font_size": 22, "bold": False,
            "spans": [{"start": 5, "end": 9, "bold": True}]}
    _, spans = RichTextDialog(None, data).result_values()
    assert spans == data["spans"]
