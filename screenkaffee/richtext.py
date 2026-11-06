"""Mixed formatting inside one text box: spans over character offsets of the plain text.

A span is {"start", "end", and any of "bold", "italic", "underline", "color", "size"}; only
attributes that differ from the box's base style are stored, so boxes without spans render exactly as before.
"""
from PySide6.QtCore import Qt, QRectF
from .i18n import N_, tr
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor, QTextDocument, QTextOption
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QPushButton, QSpinBox, QTextEdit,
                               QColorDialog, QVBoxLayout, QLabel)

SPAN_KEYS = ("bold", "italic", "underline", "color", "size")


def base_font(d):
    font = QFont(d.get("font_family", "Segoe UI"))
    font.setPixelSize(round(d.get("font_size", 22)))
    font.setBold(d.get("bold", True))
    font.setItalic(d.get("italic", False))
    font.setUnderline(d.get("underline", False))
    return font


def clean_spans(spans, length):
    """Drop invalid, empty or unknown parts so stored/loaded data can never crash rendering."""
    result = []
    for span in spans or []:
        try:
            start, end = max(0, int(span["start"])), min(length, int(span["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        attributes = {key: span[key] for key in SPAN_KEYS if key in span}
        if start < end and attributes:
            result.append({"start": start, "end": end, **attributes})
    return result


def build_document(d, font, width, alignment):
    """QTextDocument showing d["text"] with base style plus the box's spans."""
    document = QTextDocument()
    document.setDefaultFont(font)
    document.setDocumentMargin(0)
    option = QTextOption(alignment)
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    document.setDefaultTextOption(option)
    document.setTextWidth(width)
    document.setPlainText(d["text"])
    base = QTextCharFormat()
    base.setForeground(QColor(d.get("text_color", d["color"])))
    cursor = QTextCursor(document)
    cursor.select(QTextCursor.SelectionType.Document)
    cursor.mergeCharFormat(base)
    for span in clean_spans(d.get("spans"), len(d["text"])):
        cursor.setPosition(span["start"])
        cursor.setPosition(span["end"], QTextCursor.MoveMode.KeepAnchor)
        fmt = QTextCharFormat()
        if "bold" in span:
            fmt.setFontWeight(QFont.Weight.Bold if span["bold"] else QFont.Weight.Normal)
        if "italic" in span:
            fmt.setFontItalic(bool(span["italic"]))
        if "underline" in span:
            fmt.setFontUnderline(bool(span["underline"]))
        if "color" in span and QColor(span["color"]).isValid():
            fmt.setForeground(QColor(span["color"]))
        if "size" in span:
            fmt.setProperty(QTextCharFormat.Property.FontPixelSize, int(span["size"]))
        cursor.mergeCharFormat(fmt)
    return document


def draw_text(painter, rect, d, font, alignment):
    """Draw d["text"] into rect; uses plain drawText unless the box has formatting spans."""
    if not clean_spans(d.get("spans"), len(d["text"])):
        painter.setFont(font)
        painter.setPen(QColor(d.get("text_color", d["color"])))
        painter.drawText(rect, alignment | Qt.TextFlag.TextWordWrap, d["text"])
        return
    document = build_document(d, font, rect.width(), alignment)
    painter.save()
    painter.translate(rect.topLeft())
    document.drawContents(painter, QRectF(0, 0, rect.width(), rect.height()))
    painter.restore()


def spans_from_document(document, d):
    """Read formatting back from an edited document, keeping only differences from the base style."""
    font = base_font(d)
    base = {"bold": font.bold(), "italic": font.italic(), "underline": font.underline(),
            "color": QColor(d.get("text_color", d["color"])).name().lower(), "size": font.pixelSize()}
    spans = []
    block = document.begin()
    while block.isValid():
        fragment_iter = block.begin()
        while not fragment_iter.atEnd():
            fragment = fragment_iter.fragment()
            if fragment.isValid():
                fmt = fragment.charFormat()
                size = fmt.intProperty(QTextCharFormat.Property.FontPixelSize) or base["size"]
                found = {"bold": fmt.fontWeight() >= QFont.Weight.Bold, "italic": fmt.fontItalic(),
                         "underline": fmt.fontUnderline(), "color": fmt.foreground().color().name().lower() if fmt.foreground().style() != Qt.BrushStyle.NoBrush else base["color"],
                         "size": size}
                diff = {key: value for key, value in found.items() if value != base[key]}
                if diff:
                    start = fragment.position()
                    if spans and spans[-1]["end"] == start and {k: v for k, v in spans[-1].items() if k not in ("start", "end")} == diff:
                        spans[-1]["end"] = start + fragment.length()
                    else:
                        spans.append({"start": start, "end": start + fragment.length(), **diff})
            fragment_iter += 1
        block = block.next()
    return clean_spans(spans, len(document.toPlainText()))


class RichTextDialog(QDialog):
    """Edit the text of a box with bold/italic/underline/colour/size applied to the selection."""
    def __init__(self, parent, d, title=N_("Text ändern")):
        super().__init__(parent)
        self.setWindowTitle(tr(title))
        self.data = d
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("Text eingeben, Auswahl markieren und formatieren:")))
        tools = QHBoxLayout()
        self.editor = QTextEdit()
        self.editor.setAcceptRichText(False)
        self.editor.setFont(base_font(d))
        self.editor.setTextColor(QColor(d.get("text_color", d["color"])))
        for label, handler in ((tr("Fett"), self.toggle_bold), (tr("Kursiv"), self.toggle_italic),
                               (tr("Unterstrichen"), self.toggle_underline), (tr("Farbe …"), self.pick_color)):
            button = QPushButton(label)
            button.clicked.connect(handler)
            tools.addWidget(button)
        self.size = QSpinBox()
        self.size.setRange(6, 200)
        self.size.setValue(round(d.get("font_size", 22)))
        self.size.setSuffix(" px")
        apply_size = QPushButton(tr("Größe setzen"))
        apply_size.clicked.connect(lambda: self.merge(size=self.size.value()))
        tools.addWidget(self.size)
        tools.addWidget(apply_size)
        layout.addLayout(tools)
        layout.addWidget(self.editor)
        self.editor.setPlainText(d["text"])
        for span in clean_spans(d.get("spans"), len(d["text"])):
            cursor = self.editor.textCursor()
            cursor.setPosition(span["start"])
            cursor.setPosition(span["end"], QTextCursor.MoveMode.KeepAnchor)
            self.editor.setTextCursor(cursor)
            self.merge(**{key: span[key] for key in SPAN_KEYS if key in span})
        cursor = self.editor.textCursor()
        cursor.setPosition(0)
        self.editor.setTextCursor(cursor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def merge(self, bold=None, italic=None, underline=None, color=None, size=None):
        fmt = QTextCharFormat()
        if bold is not None:
            fmt.setFontWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        if italic is not None:
            fmt.setFontItalic(italic)
        if underline is not None:
            fmt.setFontUnderline(underline)
        if color is not None:
            fmt.setForeground(QColor(color))
        if size is not None:
            fmt.setProperty(QTextCharFormat.Property.FontPixelSize, int(size))
        self.editor.mergeCurrentCharFormat(fmt)

    def toggle_bold(self):
        self.merge(bold=self.editor.currentCharFormat().fontWeight() < QFont.Weight.Bold)

    def toggle_italic(self):
        self.merge(italic=not self.editor.currentCharFormat().fontItalic())

    def toggle_underline(self):
        self.merge(underline=not self.editor.currentCharFormat().fontUnderline())

    def pick_color(self):
        color = QColorDialog.getColor(self.editor.currentCharFormat().foreground().color(), self)
        if color.isValid():
            self.merge(color=color.name())

    def result_values(self):
        text = self.editor.toPlainText()
        return text, spans_from_document(self.editor.document(), self.data)
