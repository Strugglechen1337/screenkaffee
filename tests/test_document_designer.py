from PIL import Image
import zipfile
import xml.etree.ElementTree as ET
from screenkaffee.documents import DocumentOptions, export_pdf, export_docx, export_html, export_image_document
from screenkaffee.document_designer import DocumentDesigner


def test_document_comments_templates_and_order(qt_app, tmp_path):
    entries = [("Erster Schritt", Image.new("RGB", (320, 180), "red"), "Erste Beschreibung"),
               ("Zweiter Schritt", Image.new("RGB", (180, 320), "blue"), "Zweite Beschreibung")]
    designer = DocumentDesigner(entries, tmp_path)
    designer.comment.setPlainText("Geänderte Beschreibung")
    designer.list.setCurrentRow(1)
    assert designer.entries[0][2] == "Geänderte Beschreibung"
    item = designer.list.takeItem(1)
    designer.list.insertItem(0, item)
    assert designer.ordered_entries()[0][0] == "Zweiter Schritt"
    options = DocumentOptions(title="Anleitung", author="Test", footer="Intern", landscape=True)
    export_docx(designer.ordered_entries(), tmp_path / "test.docx", options=options)
    with zipfile.ZipFile(tmp_path / "test.docx") as archive:
        document = archive.read("word/document.xml").decode()
        assert "Geänderte Beschreibung" in document
        assert document.index("Zweiter Schritt") < document.index("Erster Schritt")
        assert 'w:orient="landscape"' in document
        assert "Intern" in archive.read("word/footer1.xml").decode()
        for name in archive.namelist():
            if name.endswith((".xml", ".rels")):
                ET.fromstring(archive.read(name))
    export_html(designer.ordered_entries(), tmp_path / "test.html", options=options)
    assert "Geänderte Beschreibung" in (tmp_path / "test.html").read_text(encoding="utf-8")


def test_long_comments_continue_and_image_document_is_real(qt_app, tmp_path):
    entries = [("Lang", Image.new("RGB", (300, 200), "#336699"), "Eine ausführliche Erklärung. " * 600)]
    export_pdf(entries, tmp_path / "long.pdf")
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(tmp_path / "long.pdf")
    assert len(pdf) > 1
    pdf.close()
    export_image_document([(entries[0][0], entries[0][1], "Kurz")], tmp_path / "guide.png")
    with Image.open(tmp_path / "guide.png") as image:
        assert image.width > 500 and image.height > 500


def test_document_page_break_image_order_filename_title_template_and_gif_delay(qt_app, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    entries = [("Schritt 1", Image.new("RGB", (200, 120), "red"), "Kommentar 1"),
               ("Schritt 2", Image.new("RGB", (200, 120), "green"), "Kommentar 2")]
    base_template = tmp_path / "Template.docx"
    export_docx([("Basis", Image.new("RGB", (80, 60), "white"), "")], base_template)
    opts = DocumentOptions(title="", use_filename_as_title=True, page_break=True,
                           image_before_text=True, template_path=str(base_template), gif_delay_ms=750)
    out_docx = tmp_path / "Meine_Anleitung.docx"
    export_docx(entries, out_docx, options=opts)
    with zipfile.ZipFile(out_docx) as archive:
        doc_xml = archive.read("word/document.xml").decode()
        assert "Meine Anleitung" in doc_xml
        assert 'w:br w:type="page"' in doc_xml
        assert doc_xml.index("r:embed") < doc_xml.index("Kommentar 1")

    out_html = tmp_path / "Meine_Anleitung.html"
    export_html(entries, out_html, options=opts)
    html_text = out_html.read_text(encoding="utf-8")
    assert "Meine Anleitung" in html_text and "page-break-after:always" in html_text
    assert html_text.index("<img ") < html_text.index("Kommentar 1")

    out_gif = tmp_path / "slides.gif"
    export_image_document(entries, out_gif, options=opts)
    with Image.open(out_gif) as gif_img:
        assert gif_img.info.get("duration") == 750

    designer = DocumentDesigner(entries, tmp_path)
    designer.show()
    qt_app.processEvents()
    designer.list.setCurrentRow(0)
    QTest.keyClick(designer.list, Qt.Key.Key_Delete)
    assert len(designer.ordered_entries()) == 1 and designer.ordered_entries()[0][0] == "Schritt 2"
    designer.close()
