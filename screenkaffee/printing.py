from PySide6.QtGui import QPainter
from PySide6.QtCore import QRectF
from .editor import pil_to_qimage


def print_area(viewport, dpi_x, dpi_y, margin_mm=0):
    """Printable area shrunk by a margin in millimetres (never below 10 % of the page)."""
    margin_x, margin_y = margin_mm / 25.4 * dpi_x, margin_mm / 25.4 * dpi_y
    margin_x, margin_y = min(margin_x, viewport.width() * .45), min(margin_y, viewport.height() * .45)
    return QRectF(viewport.x() + margin_x, viewport.y() + margin_y, viewport.width() - 2 * margin_x, viewport.height() - 2 * margin_y)


def paint_images(printer, images, margin_mm=0):
    painter = QPainter(printer)
    if not painter.isActive():
        raise RuntimeError("Druckauftrag konnte nicht gestartet werden.")
    try:
        for index, image in enumerate(images):
            if index:
                printer.newPage()
            area = print_area(QRectF(painter.viewport()), printer.logicalDpiX(), printer.logicalDpiY(), margin_mm)
            scale = min(area.width() / image.width, area.height() / image.height)
            target = QRectF(area.left() + (area.width() - image.width * scale) / 2,
                area.top() + (area.height() - image.height * scale) / 2, image.width * scale, image.height * scale)
            painter.drawImage(target, pil_to_qimage(image))
    finally:
        painter.end()


def print_images(parent, images, margin_mm=0):
    from PySide6.QtPrintSupport import QPrinter, QPrintDialog
    from PySide6.QtWidgets import QDialog
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    dialog = QPrintDialog(printer, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        paint_images(printer, images, margin_mm)
