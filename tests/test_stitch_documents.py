import zipfile
import xml.etree.ElementTree as ET
import numpy as np
import pytest
from PIL import Image
from screenkaffee.stitch import stitch_vertical
from screenkaffee.documents import export_html, export_docx, export_pdf


def test_stitch_restores_a_long_page_and_rejects_no_evidence():
    rng = np.random.default_rng(3)
    long = Image.fromarray(rng.integers(0, 256, (1000, 240, 3), dtype=np.uint8))
    a = long.crop((0, 0, 240, 600))
    b = long.crop((0, 400, 240, 1000))
    assert stitch_vertical([a, b]).tobytes() == long.tobytes()
    with pytest.raises(ValueError):
        stitch_vertical([Image.new("RGB", (200, 400), "white")] * 2)


def test_document_exports_are_valid_and_offline(tmp_path, qt_app):
    entries = [("A < B & C", Image.new("RGB", (320, 180), "#336699")),
               ("Zweite Aufnahme", Image.new("RGB", (180, 320), "white"))]
    export_html(entries, tmp_path / "test.html")
    text = (tmp_path / "test.html").read_text(encoding="utf-8")
    assert "A &lt; B &amp; C" in text
    assert text.count("data:image/png;base64,") == 2
    export_docx(entries, tmp_path / "test.docx")
    with zipfile.ZipFile(tmp_path / "test.docx") as doc:
        for name in doc.namelist():
            if name.endswith((".xml", ".rels")):
                ET.fromstring(doc.read(name))
        assert "word/media/image2.png" in doc.namelist()
    export_pdf(entries, tmp_path / "test.pdf")
    assert (tmp_path / "test.pdf").read_bytes().startswith(b"%PDF")
