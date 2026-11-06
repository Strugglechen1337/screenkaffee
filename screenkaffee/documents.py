"""Offline image documentation export (HTML, PDF via Qt, minimal DOCX)."""
import base64
import html
import io
from pathlib import Path
import zipfile
import re
from dataclasses import dataclass, asdict


@dataclass
class DocumentOptions:
    title: str = "Aufnahmen"
    author: str = ""
    font_family: str = "Sans Serif"
    font_size: int = 11
    accent: str = "#172033"
    landscape: bool = False
    numbered_steps: bool = True
    page_numbers: bool = True
    footer: str = ""
    image_border: bool = True
    page_break: bool = True
    image_before_text: bool = True
    use_filename_as_title: bool = True
    template_path: str = ""
    gif_delay_ms: int = 1500

    def __post_init__(self):
        if not isinstance(self.font_size, int) or not 6 <= self.font_size <= 72:
            raise ValueError("Ungültige Dokumentschriftgröße.")
        if not re.fullmatch(r"#[A-Fa-f0-9]{6}", self.accent):
            raise ValueError("Ungültige Dokumentfarbe.")
        if not isinstance(self.gif_delay_ms, int) or not 100 <= self.gif_delay_ms <= 60000:
            raise ValueError("Ungültige GIF-Anzeigedauer.")


def normalize_entries(entries, options=None):
    out = []
    for index, entry in enumerate(entries, 1):
        label = entry[0] if entry[0] or (options and not options.use_filename_as_title) else f"Aufnahme {index}"
        out.append((label, entry[1], entry[2] if len(entry) > 2 else ""))
    return out
from .color import save_png


def png_bytes(image):
    buf = io.BytesIO()
    save_png(image, buf)
    return buf.getvalue()


def export_html(entries, path, title="Aufnahmen", options=None):
    options = options or DocumentOptions(title=title)
    doc_title = options.title or (Path(path).stem.replace("_", " ") if options.use_filename_as_title else title)
    sections = []
    for index, (label, image, comment) in enumerate(normalize_entries(entries, options), 1):
        b64 = base64.b64encode(png_bytes(image)).decode("ascii")
        prefix = f"{index}. " if options.numbered_steps else ""
        img_tag = f'<img alt="{html.escape(label, quote=True)}" src="data:image/png;base64,{b64}">'
        txt_tag = f'<p>{html.escape(comment).replace(chr(10), "<br>")}</p>'
        content = (img_tag + txt_tag) if options.image_before_text else (txt_tag + img_tag)
        brk = ' style="page-break-after:always"' if options.page_break else ""
        sections.append(f'<section{brk}><h2>{prefix}{html.escape(label)}</h2>{content}</section>')
    doc = f'''<!doctype html><html lang="de"><meta charset="utf-8"><title>{html.escape(doc_title)}</title>
<style>body{{font:{options.font_size}pt "{html.escape(options.font_family, quote=True)}",sans-serif;max-width:1000px;margin:3rem auto;padding:0 2rem;color:{options.accent}}}h1{{font-size:2rem}}section{{margin:3rem 0;break-inside:avoid}}img{{max-width:100%;border:{'1px solid #ddd' if options.image_border else '0'}}}p{{line-height:1.5}}@media print{{body{{margin:0}}}}</style>
<h1>{html.escape(doc_title)}</h1><p>{html.escape(options.author)}</p>{''.join(sections)}<footer>{html.escape(options.footer)}</footer></html>'''
    Path(path).write_text(doc, encoding="utf-8")
    return Path(path)


def export_pdf(entries, path, title="Aufnahmen", options=None):
    from PySide6.QtGui import QPdfWriter, QPainter, QFont, QPageSize, QColor, QPageLayout, QTextDocument
    from PySide6.QtCore import QRectF, Qt
    from .editor import pil_to_qimage
    options = options or DocumentOptions(title=title)
    writer = QPdfWriter(str(path))
    writer.setTitle(options.title)
    writer.setCreator("screenkaffee")
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageOrientation(QPageLayout.Orientation.Landscape if options.landscape else QPageLayout.Orientation.Portrait)
    writer.setResolution(144)
    painter = QPainter(writer)
    if not painter.isActive():
        raise RuntimeError("PDF-Datei konnte nicht geöffnet werden.")
    try:
        entries = normalize_entries(entries)
        page_number = 0
        for index, (label, image, comment) in enumerate(entries):
            if index:
                writer.newPage()
            page_number += 1
            painter.setPen(QColor(options.accent))
            painter.setFont(QFont(options.font_family, 16, QFont.Weight.Bold))
            prefix = f"{index + 1}. " if options.numbered_steps else ""
            painter.drawText(QRectF(0, 0, writer.width(), 100), Qt.TextFlag.TextWordWrap, prefix + label)
            comment_doc = QTextDocument()
            comment_doc.setDocumentMargin(0)
            comment_doc.setDefaultFont(QFont(options.font_family, options.font_size))
            comment_doc.setPlainText(comment)
            comment_doc.setTextWidth(writer.width())
            comment_height = comment_doc.size().height() * 1.5 if comment else 0
            first_comment = min(comment_height, writer.height() * .25)
            available = QRectF(0, 120, writer.width(), writer.height() - 200 - first_comment)
            scale = min(available.width() / image.width, available.height() / image.height)
            painter.drawImage(QRectF(0, available.top(), image.width * scale, image.height * scale), pil_to_qimage(image))
            if options.image_border:
                painter.drawRect(QRectF(0, available.top(), image.width * scale, image.height * scale))
            painter.setFont(QFont(options.font_family, 9))
            painter.drawText(QRectF(0, writer.height() - 35, writer.width(), 30), Qt.AlignmentFlag.AlignRight,
                (options.footer + " · " if options.footer else "") + (str(page_number) if options.page_numbers else ""))
            if comment:
                # Render a text document in page slices so long comments are
                # continued instead of silently clipped or lost.
                offset, y = 0, available.bottom() + 12
                while offset < comment_doc.size().height():
                    height = (writer.height() - y - 60) / 1.5
                    painter.save()
                    painter.translate(0, y)
                    painter.scale(1.5, 1.5)
                    painter.setClipRect(QRectF(0, 0, writer.width() / 1.5, height))
                    painter.translate(0, -offset)
                    comment_doc.setTextWidth(writer.width() / 1.5)
                    comment_doc.drawContents(painter, QRectF(0, offset, writer.width() / 1.5, height))
                    painter.restore()
                    offset += height
                    if offset < comment_doc.size().height():
                        writer.newPage()
                        page_number += 1
                        y = 30
    finally:
        painter.end()


def export_docx(entries, path, title="Aufnahmen", options=None):
    options = options or DocumentOptions(title=title)
    doc_title = options.title or (Path(path).stem.replace("_", " ") if options.use_filename_as_title else title)
    entries = normalize_entries(entries, options)
    # OOXML package with inline PNG drawings. Custom Template.docx styles are preserved when provided.
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    body, relationships = [], []
    body.append(f'<w:p><w:r><w:rPr><w:b/><w:sz w:val="36"/></w:rPr><w:t>{html.escape(doc_title)}</w:t></w:r></w:p>')
    if options.author:
        body.append(f'<w:p><w:r><w:t>{html.escape(options.author)}</w:t></w:r></w:p>')
    template_styles = None
    if options.template_path and Path(options.template_path).is_file():
        try:
            with zipfile.ZipFile(options.template_path, "r") as tpl:
                if "word/styles.xml" in tpl.namelist():
                    template_styles = tpl.read("word/styles.xml")
        except Exception:
            template_styles = None
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, (label, image, comment) in enumerate(entries, 1):
            rid = f"rId{index}"
            relationships.append(f'<Relationship Id="{rid}" Type="{r}/image" Target="media/image{index}.png"/>')
            archive.writestr(f"word/media/image{index}.png", png_bytes(image))
            scale = min((8_900_000 if options.landscape else 5_900_000) / image.width, (5_500_000 if options.landscape else 7_900_000) / image.height)
            cx, cy = round(image.width * scale), round(image.height * scale)
            prefix = f"{index}. " if options.numbered_steps else ""
            body.append(f'<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>{prefix}{html.escape(label)}</w:t></w:r></w:p>')
            drawing_p = f'''<w:p><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">
<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{index}" name="Aufnahme {index}"/>
<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>
<pic:nvPicPr><pic:cNvPr id="{index}" name="image{index}.png"/><pic:cNvPicPr/></pic:nvPicPr>
<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>
</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'''
            comment_ps = [f'<w:p><w:r><w:t xml:space="preserve">{html.escape(paragraph)}</w:t></w:r></w:p>' for paragraph in comment.splitlines()]
            if options.image_before_text:
                body.append(drawing_p)
                body.extend(comment_ps)
            else:
                body.extend(comment_ps)
                body.append(drawing_p)
            if index < len(entries) and options.page_break:
                body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        page_size = 'w:w="16838" w:h="11906" w:orient="landscape"' if options.landscape else 'w:w="11906" w:h="16838"'
        footer_reference = '<w:footerReference w:type="default" r:id="footer"/>' if options.footer or options.page_numbers else ""
        document = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{w}" xmlns:r="{r}" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>{''.join(body)}<w:sectPr>{footer_reference}<w:pgSz {page_size}/><w:pgMar w:top="1134" w:bottom="1134" w:left="1134" w:right="1134"/></w:sectPr></w:body></w:document>'''
        archive.writestr("word/document.xml", document)
        relationships.append(f'<Relationship Id="styles" Type="{r}/styles" Target="styles.xml"/>')
        if footer_reference:
            relationships.append(f'<Relationship Id="footer" Type="{r}/footer" Target="footer1.xml"/>')
            page_field = '<w:fldSimple w:instr="PAGE"><w:r><w:t>1</w:t></w:r></w:fldSimple>' if options.page_numbers else ""
            archive.writestr("word/footer1.xml", f'<w:ftr xmlns:w="{w}"><w:p><w:pPr><w:jc w:val="right"/></w:pPr><w:r><w:t xml:space="preserve">{html.escape(options.footer)} </w:t></w:r>{page_field}</w:p></w:ftr>')
        if template_styles is not None:
            archive.writestr("word/styles.xml", template_styles)
        else:
            archive.writestr("word/styles.xml", f'<w:styles xmlns:w="{w}"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="{html.escape(options.font_family, quote=True)}" w:hAnsi="{html.escape(options.font_family, quote=True)}"/><w:sz w:val="{options.font_size * 2}"/></w:rPr></w:rPrDefault></w:docDefaults></w:styles>')
        archive.writestr("word/_rels/document.xml.rels", f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{"".join(relationships)}</Relationships>')
        archive.writestr("_rels/.rels", f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{r}/officeDocument" Target="word/document.xml"/></Relationships>')
        footer_type = '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>' if footer_reference else ""
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/06/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>' + footer_type + '</Types>'.replace("package/06/", "package/2006/"))
    return Path(path)


def export_image_document(entries, path, title="Aufnahmen", options=None):
    import pypdfium2 as pdfium
    from PIL import Image
    import tempfile
    import os
    handle, name = tempfile.mkstemp(suffix=".pdf", prefix="screenkaffee-document-")
    os.close(handle)
    document = None
    try:
        export_pdf(entries, name, title=title, options=options)
        document = pdfium.PdfDocument(name)
        count = len(document)
        if count < 1:
            raise RuntimeError("Dokument konnte nicht gerendert werden.")
        pages = []
        total_pixels = 0
        for index in range(count):
            page = document[index]
            bitmap = page.render(scale=1240 / page.get_width())
            try:
                image = bitmap.to_pil().convert("RGB")
                total_pixels += image.width * image.height
                if total_pixels > 150_000_000:
                    raise ValueError("Bilddokument überschreitet 150 Megapixel.")
                pages.append(image.copy())
            finally:
                bitmap.close()
                page.close()
        if str(path).lower().endswith(".gif"):
            delay = options.gif_delay_ms if options else 1500
            pages[0].save(path, save_all=True, append_images=pages[1:], duration=delay, loop=0)
        else:
            width, height = max(p.width for p in pages), sum(p.height for p in pages)
            if width * height > 150_000_000:
                raise ValueError("Bilddokument überschreitet 150 Megapixel.")
            canvas = Image.new("RGB", (width, height), "white")
            offset = 0
            for page in pages:
                canvas.paste(page, (0, offset))
                offset += page.height
            if str(path).lower().endswith(".png"):
                save_png(canvas, path)
            else:
                canvas.save(path, quality=95)
    finally:
        if document:
            document.close()
        Path(name).unlink(missing_ok=True)
    return Path(path)
