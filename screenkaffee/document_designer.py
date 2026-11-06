"""Reusable document templates, image order, per-image comments and preview."""
from dataclasses import asdict
import json
from pathlib import Path
import uuid
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QListWidget,
    QListWidgetItem, QLineEdit, QTextEdit, QLabel, QSpinBox, QCheckBox, QPushButton,
    QFileDialog, QMessageBox, QAbstractItemView, QScrollArea)
from .documents import DocumentOptions, export_pdf, export_docx, export_html, export_image_document
from .editor import pil_to_qimage
from .i18n import tr


class DocumentDesigner(QDialog):
    def __init__(self, entries, root, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("screenkaffee · Dokument gestalten"))
        self.resize(1150, 800)
        self.root = Path(root)
        self.entries = [[entry[0], entry[1], entry[2] if len(entry) > 2 else ""] for entry in entries]
        self.current = None
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.title, self.author, self.footer = QLineEdit(tr("Anleitung")), QLineEdit(), QLineEdit()
        default_tpl = self.root / "Template.docx"
        self.template_path = QLineEdit(str(default_tpl) if default_tpl.is_file() else "")
        self.template_path.setPlaceholderText(tr("Optional: Template.docx"))
        form = QFormLayout()
        form.addRow(tr("Dokumenttitel"), self.title)
        form.addRow(tr("Autor"), self.author)
        form.addRow(tr("Fußzeile"), self.footer)
        form.addRow(tr("Word-Vorlage"), self.template_path)
        header.addLayout(form, 1)
        style = QFormLayout()
        self.font = QLineEdit("Sans Serif")
        self.font_size = QSpinBox()
        self.font_size.setRange(6, 72)
        self.font_size.setValue(11)
        self.accent = QLineEdit("#172033")
        self.gif_delay = QSpinBox()
        self.gif_delay.setRange(100, 60000)
        self.gif_delay.setSingleStep(100)
        self.gif_delay.setSuffix(" ms")
        self.gif_delay.setValue(1500)
        style.addRow(tr("Schrift"), self.font)
        style.addRow(tr("Schriftgröße"), self.font_size)
        style.addRow(tr("Akzentfarbe"), self.accent)
        style.addRow(tr("GIF-Verzögerung"), self.gif_delay)
        header.addLayout(style, 1)
        flags = QVBoxLayout()
        self.landscape = QCheckBox(tr("Querformat"))
        self.numbered = QCheckBox(tr("Schritte nummerieren"))
        self.page_numbers = QCheckBox(tr("Seitenzahlen"))
        self.border = QCheckBox(tr("Bildrahmen"))
        self.page_break = QCheckBox(tr("Seitenumbruch je Aufnahme"))
        self.image_before_text = QCheckBox(tr("Bild vor Beschreibung"))
        self.use_filename_as_title = QCheckBox(tr("Standardtitel wenn leer"))
        for box in (self.numbered, self.page_numbers, self.border, self.page_break, self.image_before_text, self.use_filename_as_title):
            box.setChecked(True)
        for box in (self.landscape, self.numbered, self.page_numbers, self.border, self.page_break, self.image_before_text, self.use_filename_as_title):
            flags.addWidget(box)
        header.addLayout(flags)
        layout.addLayout(header)
        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        for index, entry in enumerate(entries):
            item = QListWidgetItem(entry[0])
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.list.addItem(item)
        self.list.currentItemChanged.connect(self.select_entry)
        self.del_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.list)
        self.del_shortcut.activated.connect(self.remove_current_entry)
        body.addWidget(self.list, 1)
        editing = QVBoxLayout()
        self.preview = QLabel()
        self.preview.setMinimumSize(550, 300)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        editing.addWidget(self.preview, 1)
        self.entry_title = QLineEdit()
        editing.addWidget(self.entry_title)
        self.comment = QTextEdit()
        self.comment.setPlaceholderText(tr("Beschreibung / Anweisung zu dieser Aufnahme …"))
        editing.addWidget(self.comment)
        body.addLayout(editing, 2)
        layout.addLayout(body, 1)
        actions = QHBoxLayout()
        for label, callback in [(tr("Vorlage laden …"), self.load_template), (tr("Vorlage speichern …"), self.save_template),
                                (tr("PDF-Vorschau"), self.preview_pdf), (tr("Dokument exportieren …"), self.export)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        close = QPushButton(tr("Schließen"))
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.status = QLabel(tr("Bilder links ziehen, um ihre Reihenfolge zu ändern."))
        layout.addWidget(self.status)
        if self.list.count():
            self.list.setCurrentRow(0)

    def save_entry(self):
        if self.current is not None:
            self.entries[self.current][0] = self.entry_title.text()
            self.entries[self.current][2] = self.comment.toPlainText()

    def select_entry(self, current, previous):
        self.save_entry()
        if current is None:
            return
        self.current = current.data(Qt.ItemDataRole.UserRole)
        title, image, comment = self.entries[self.current]
        self.entry_title.setText(title)
        self.comment.setPlainText(comment)
        self.preview.setPixmap(QPixmap.fromImage(pil_to_qimage(image)).scaled(650, 350, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def remove_current_entry(self):
        row = self.list.currentRow()
        if row >= 0 and self.list.count() > 1:
            self.current = None
            self.list.takeItem(row)
            if self.list.count():
                self.list.setCurrentRow(min(row, self.list.count() - 1))

    def ordered_entries(self):
        self.save_entry()
        return [self.entries[self.list.item(row).data(Qt.ItemDataRole.UserRole)] for row in range(self.list.count())]

    def options(self):
        return DocumentOptions(self.title.text(), self.author.text(), self.font.text(), self.font_size.value(),
            self.accent.text(), self.landscape.isChecked(), self.numbered.isChecked(),
            self.page_numbers.isChecked(), self.footer.text(), self.border.isChecked(),
            self.page_break.isChecked(), self.image_before_text.isChecked(),
            self.use_filename_as_title.isChecked(), self.template_path.text().strip(), self.gif_delay.value())

    def save_template(self):
        folder = self.root / "templates"
        folder.mkdir(exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, tr("Dokumentvorlage speichern"), str(folder / "Anleitung.json"), tr("Vorlage (*.json)"))
        if path:
            try:
                data = {"format": "screenkaffee-document-template", "version": 1, "options": asdict(self.options())}
                Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                QMessageBox.warning(self, tr("Vorlage konnte nicht gespeichert werden"), str(exc))

    def load_template(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Dokumentvorlage laden"), str(self.root / "templates"), tr("Vorlage (*.json)"))
        if path:
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                if data.get("format") != "screenkaffee-document-template" or data.get("version") != 1:
                    raise ValueError(tr("Unbekannte Dokumentvorlage."))
                options = DocumentOptions(**data["options"])
                for widget, value in [(self.title, options.title), (self.author, options.author), (self.footer, options.footer),
                                       (self.font, options.font_family), (self.accent, options.accent), (self.template_path, options.template_path)]:
                    widget.setText(value)
                self.font_size.setValue(options.font_size)
                self.gif_delay.setValue(options.gif_delay_ms)
                for widget, value in [(self.landscape, options.landscape), (self.numbered, options.numbered_steps),
                                      (self.page_numbers, options.page_numbers), (self.border, options.image_border),
                                      (self.page_break, options.page_break), (self.image_before_text, options.image_before_text),
                                      (self.use_filename_as_title, options.use_filename_as_title)]:
                    widget.setChecked(value)
            except Exception as exc:
                QMessageBox.warning(self, tr("Vorlage konnte nicht geladen werden"), str(exc))

    def preview_pdf(self):
        try:
            import pypdfium2 as pdfium
            folder = self.root / "document-previews"
            folder.mkdir(exist_ok=True)
            path = folder / (uuid.uuid4().hex + ".pdf")
            export_pdf(self.ordered_entries(), path, options=self.options())
            dialog = QDialog(self)
            dialog.resize(900, 800)
            dialog.setWindowTitle(tr("Dokumentvorschau"))
            layout = QVBoxLayout(dialog)
            view = QScrollArea()
            view.setWidgetResizable(True)
            content = QWidget()
            pages = QVBoxLayout(content)
            with pdfium.PdfDocument(path) as document:
                for index in range(len(document)):
                    page = document[index]
                    bitmap = page.render(scale=800 / page.get_width())
                    try:
                        label = QLabel()
                        label.setPixmap(QPixmap.fromImage(pil_to_qimage(bitmap.to_pil())))
                        pages.addWidget(label)
                    finally:
                        bitmap.close()
                        page.close()
            view.setWidget(content)
            layout.addWidget(view)
            dialog.exec()
        except Exception as exc:
            QMessageBox.warning(self, tr("Vorschau fehlgeschlagen"), str(exc))

    def export(self):
        path, selected_filter = QFileDialog.getSaveFileName(self, tr("Dokument exportieren"), "Anleitung.pdf",
            "PDF (*.pdf);;Word (*.docx);;HTML (*.html);;PNG (*.png);;JPEG (*.jpg);;GIF (*.gif)")
        if not path:
            return
        try:
            extension = Path(path).suffix.lower()
            if not extension:
                import re
                extension = re.search(r"\*([.a-z]+)", selected_filter).group(1)
                path += extension
            exporters = {".pdf": export_pdf, ".docx": export_docx, ".html": export_html,
                ".png": export_image_document, ".jpg": export_image_document, ".gif": export_image_document}
            exporters[extension](self.ordered_entries(), path, options=self.options())
            self.status.setText(tr("Gespeichert: {path}", path=path))
        except Exception as exc:
            QMessageBox.warning(self, tr("Export fehlgeschlagen"), str(exc))
