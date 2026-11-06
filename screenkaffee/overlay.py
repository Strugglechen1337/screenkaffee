import os
from .i18n import N_, tr
import sys
from PIL import ImageDraw
from PySide6.QtCore import Qt, QPoint, QPointF, QRect, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QCursor, QPixmap, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import QWidget
from .editor import pil_to_qimage

HELP_LINES = (N_("Bereich ziehen oder Fenster anklicken"), N_("Mausrad: vergrößern / verkleinern"), N_("M: Mauszeiger in Aufnahme ein/aus"),
              N_("Umschalt beim Loslassen: Feinauswahl (Pfeile, Strg+Pfeile, Enter)"), N_("Doppelklick: ganzer Bildschirm"), N_("L: Lineal (Abstände messen)"), N_("H: Hilfe ein/aus"), N_("Esc oder rechte Maustaste: abbrechen"))


def _is_real_win32_display():
    return sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen"


def _force_win32_foreground(hwnd):
    if not _is_real_win32_display() or not hwnd:
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        fg = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        cur_tid = kernel32.GetCurrentThreadId()
        attached = False
        if fg_tid and fg_tid != cur_tid:
            attached = bool(user32.AttachThreadInput(fg_tid, cur_tid, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(fg_tid, cur_tid, False)
    except Exception:
        pass


def draw_cursor_arrow(image, x, y):
    """Composite a crisp standard pointer arrow onto a copy of image at pixel (x, y)."""
    out = image.copy()
    draw = ImageDraw.Draw(out)
    poly = [(x, y), (x, y + 17), (x + 4, y + 13), (x + 7, y + 20), (x + 10, y + 19), (x + 7, y + 12), (x + 12, y + 12)]
    draw.polygon(poly, fill=(255, 255, 255), outline=(15, 23, 42))
    return out


class SelectionOverlay(QWidget):
    selected = Signal(object)
    regionSelected = Signal(object)
    colorSelected = Signal(str)
    windowSelected = Signal(object)
    cursorToggled = Signal(bool)
    cancelled = Signal()
    MAX_ZOOM = 8.0
    ESC_HOTKEY_ID = 0x5059

    def __init__(self, image, geometry, parent=None, show_cursor=False, cursor_pos=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(geometry)
        self.base_image = image
        self.image = image
        self.pixmap = QPixmap.fromImage(pil_to_qimage(image))
        self.start = None
        cursor = QCursor.pos() - geometry.topLeft()
        self.end = cursor if QRect(0, 0, geometry.width(), geometry.height()).contains(cursor) else QPoint(0, 0)
        self.cursor_pos = cursor_pos
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.done = False
        self.windows = []
        self.window_handles = []
        self.hover_rect = None
        self.pick_color = False
        self.measure_mode = False
        self.measure_line = None           # [Start, Ende] in Overlay-Koordinaten
        self.measuring = False
        self.precise_box = None
        self.zoom = 1.0
        self.origin = QPointF(0, 0)        # overlay coordinates (unzoomed) shown at the top-left corner
        self.show_help = True
        self.show_cursor = show_cursor
        self._esc_hotkey_registered = False
        self._keyboard_grabbed = False
        self._esc_was_down = False
        self._esc_poll_timer = QTimer(self)
        self._esc_poll_timer.setInterval(25)
        self._esc_poll_timer.timeout.connect(self._poll_escape_key)
        self.esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.cancel)
        self.esc_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        if self.show_cursor:
            self.refresh_cursor_layer()

    def showEvent(self, event):
        super().showEvent(event)
        self._acquire_escape_and_focus()
        QTimer.singleShot(0, self._acquire_escape_and_focus)

    def hideEvent(self, event):
        self._release_escape_and_focus()
        super().hideEvent(event)

    def _acquire_escape_and_focus(self):
        if self.done or not self.isVisible():
            return
        if _is_real_win32_display():
            hwnd = int(self.winId())
            _force_win32_foreground(hwnd)
            if not self._esc_hotkey_registered:
                try:
                    import ctypes
                    self._esc_was_down = bool(ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000)
                    self._esc_hotkey_registered = bool(
                        ctypes.windll.user32.RegisterHotKey(hwnd, self.ESC_HOTKEY_ID, 0x4000, 0x1B)
                    )
                except Exception:
                    pass
            if not self._esc_poll_timer.isActive():
                self._esc_poll_timer.start()
            if not self._keyboard_grabbed:
                try:
                    self.grabKeyboard()
                    self._keyboard_grabbed = True
                except Exception:
                    pass
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _release_escape_and_focus(self):
        self._esc_poll_timer.stop()
        if self._esc_hotkey_registered and _is_real_win32_display():
            try:
                import ctypes
                ctypes.windll.user32.UnregisterHotKey(int(self.winId()), self.ESC_HOTKEY_ID)
            except Exception:
                pass
            self._esc_hotkey_registered = False
        if self._keyboard_grabbed:
            try:
                self.releaseKeyboard()
            except Exception:
                pass
            self._keyboard_grabbed = False

    def _poll_escape_key(self):
        if self.done or not _is_real_win32_display():
            return
        try:
            import ctypes
            down = bool(ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000)
            if down and not self._esc_was_down:
                self.cancel()
                return
            self._esc_was_down = down
        except Exception:
            pass

    def nativeEvent(self, event_type, message):
        if _is_real_win32_display():
            try:
                import ctypes
                from ctypes import wintypes
                msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
                if msg.message == 0x0312 and msg.wParam == self.ESC_HOTKEY_ID:
                    self.cancel()
                    return True, 0
            except Exception:
                pass
        return super().nativeEvent(event_type, message)

    def refresh_cursor_layer(self):
        """Update the live freeze-frame when M toggles the mouse cursor on or off."""
        if self.show_cursor:
            pt = self.cursor_pos
            if pt is None:
                px = self.pixel(QPointF(self.end))
                pt = (max(0, min(self.base_image.width - 1, round(px.x()))),
                      max(0, min(self.base_image.height - 1, round(px.y()))))
                self.cursor_pos = pt
            self.image = draw_cursor_arrow(self.base_image, pt[0], pt[1])
        else:
            self.image = self.base_image.copy()
        self.pixmap = QPixmap.fromImage(pil_to_qimage(self.image))

    # --- view <-> content mapping -------------------------------------------------
    def scale(self):
        return self.image.width / max(1, self.width()), self.image.height / max(1, self.height())

    def to_content(self, point):
        """Widget point -> unzoomed overlay coordinates."""
        return QPointF(self.origin.x() + point.x() / self.zoom, self.origin.y() + point.y() / self.zoom)

    def to_view(self, point):
        return QPointF((point.x() - self.origin.x()) * self.zoom, (point.y() - self.origin.y()) * self.zoom)

    def pixel(self, point):
        """Widget point -> original image pixel (float)."""
        content = self.to_content(point)
        sx, sy = self.scale()
        return QPointF(content.x() * sx, content.y() * sy)

    def source_rect(self):
        sx, sy = self.scale()
        return QRectF(self.origin.x() * sx, self.origin.y() * sy, self.width() / self.zoom * sx, self.height() / self.zoom * sy)

    def view_rect(self, rect):
        top_left, bottom_right = self.to_view(QPointF(rect.topLeft())), self.to_view(QPointF(rect.bottomRight() + QPoint(1, 1)))
        return QRect(top_left.toPoint(), bottom_right.toPoint())

    def set_zoom(self, zoom, anchor):
        """Zoom around a widget point so the pixel under it stays put."""
        zoom = max(1.0, min(self.MAX_ZOOM, zoom))
        content = self.to_content(anchor)
        self.zoom = zoom
        x = content.x() - anchor.x() / zoom
        y = content.y() - anchor.y() / zoom
        self.origin = QPointF(max(0, min(self.width() - self.width() / zoom, x)), max(0, min(self.height() - self.height() / zoom, y)))
        self.update()

    def wheelEvent(self, event):
        if self.start is not None or self.precise_box:
            return
        step = 1.25 if event.angleDelta().y() > 0 else 1 / 1.25
        self.set_zoom(self.zoom * step, event.position())
        event.accept()

    def window_at(self, view_point):
        content = self.to_content(view_point).toPoint()
        return next((index for index, (title, rect) in enumerate(self.windows) if rect.contains(content)), None)

    # --- painting ---------------------------------------------------------------------
    def _draw_full_pixmap(self, painter, source):
        if self.zoom == 1.0:
            dpr = self.devicePixelRatioF() or 1.0
            painter.save()
            painter.scale(1.0 / dpr, 1.0 / dpr)
            painter.drawPixmap(0, 0, self.pixmap)
            painter.restore()
        else:
            painter.drawPixmap(QRectF(self.rect()), self.pixmap, source)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom < 2.0)
        source = self.source_rect()
        self._draw_full_pixmap(painter, source)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 35 if self.measure_mode else 110))
        if self.windows and self.hover_rect is not None and self.start is None:
            hover = self.view_rect(self.hover_rect)
            painter.save()
            painter.setClipRect(hover)
            self._draw_full_pixmap(painter, source)
            painter.restore()
            painter.setPen(QPen(QColor("#61e7c5"), 3))
            painter.drawRect(hover)
        if self.start is not None:
            rect = QRect(self.start, self.end).normalized().intersected(self.rect())
            painter.save()
            painter.setClipRect(rect)
            self._draw_full_pixmap(painter, source)
            painter.restore()
            painter.setPen(QPen(QColor("#61e7c5"), 2))
            painter.drawRect(rect)
            sx, sy = self.scale()
            painter.setFont(QFont("Segoe UI", 11))
            painter.drawText(rect.topLeft() + QPoint(4, -8 if rect.top() > 30 else 22),
                             f"{round(rect.width() * sx / self.zoom)} × {round(rect.height() * sy / self.zoom)} px")
        if self.measure_mode and self.measure_line:
            from . import ruler
            start, end = self.measure_line
            a, b = self.pixel(QPointF(start)), self.pixel(QPointF(end))
            ruler.paint(painter, QPointF(start), QPointF(end), ruler.measurement(a.x(), a.y(), b.x(), b.y()), guides=QRectF(self.rect()))
        painter.setPen(QColor("white"))
        painter.setFont(QFont("Segoe UI", 12))
        if self.measure_mode:
            painter.fillRect(QRect(18, 20, 900, 32), QColor(0, 0, 0, 150))
            painter.drawText(28, 42, tr("Lineal: ziehen zum Messen · Umschalt: gerade / 45° · Strg+C: Maße kopieren · L: zurück zur Auswahl · Esc: schließen"))
        elif self.precise_box:
            painter.drawText(28, 42, tr("Pfeile: Größe ±1 px · Strg+Pfeile: verschieben · Enter: aufnehmen · Esc: abbrechen"))
        elif self.show_help and self.start is None:
            for index, line in enumerate(HELP_LINES):
                painter.drawText(28, 42 + index * 22, tr(line))
            painter.drawText(28, 42 + len(HELP_LINES) * 22, tr('Mauszeiger in Aufnahmen: {state} · Zoom {zoom}×', state=tr('an') if self.show_cursor else tr('aus'), zoom=format(self.zoom, '.2g')))
        elif self.start is None:
            painter.drawText(28, 42, tr("H: Hilfe"))
        pos = self.end
        painter.setPen(QPen(QColor("#61e7c5"), 1))
        painter.drawLine(pos.x(), 0, pos.x(), self.height())
        painter.drawLine(0, pos.y(), self.width(), pos.y())
        # Magnifier is sampled from original pixels, never from the overlay.
        pixel = self.pixel(QPointF(pos))
        cx, cy = max(0, min(self.image.width - 1, int(pixel.x()))), max(0, min(self.image.height - 1, int(pixel.y())))
        sample = self.pixmap.copy(max(0, cx - 12), max(0, cy - 12), 25, 25)
        target = QRect(min(pos.x() + 24, self.width() - 136), min(pos.y() + 24, self.height() - 156), 116, 116)
        painter.drawPixmap(target, sample)
        mcx, mcy = target.center().x(), target.center().y()
        painter.setPen(QPen(QColor(37, 99, 235, 210), 1))
        painter.drawLine(target.left(), mcy, target.right(), mcy)
        painter.drawLine(mcx, target.top(), mcx, target.bottom())
        painter.setPen(QPen(QColor("#61e7c5"), 2))
        painter.drawRect(target)
        footer = QRect(target.left(), target.bottom() + 2, target.width(), 22)
        painter.fillRect(footer, QColor(18, 20, 23, 220))
        painter.setPen(QColor("#f0f4f8"))
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        painter.drawText(footer, Qt.AlignmentFlag.AlignCenter, f"{cx}, {cy} px")

    # --- mouse -----------------------------------------------------------------------
    def mousePressEvent(self, event):
        if self.done:
            return
        if self.pick_color and event.button() == Qt.MouseButton.LeftButton:
            pixel = self.pixel(event.position())
            x = max(0, min(self.image.width - 1, round(pixel.x())))
            y = max(0, min(self.image.height - 1, round(pixel.y())))
            self.done = True
            self._release_escape_and_focus()
            self.colorSelected.emit("#%02X%02X%02X" % self.image.convert("RGB").getpixel((x, y)))
            self.close()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.cancel()
        elif event.button() == Qt.MouseButton.LeftButton and self.measure_mode:
            point = event.position()
            self.measure_line = [QPointF(point), QPointF(point)]
            self.measuring = True
            self.update()
        elif event.button() == Qt.MouseButton.LeftButton:
            self.start = event.position().toPoint()
            self.end = self.start
            self.update()

    def mouseMoveEvent(self, event):
        if self.done or self.precise_box:
            return
        self.end = event.position().toPoint()
        if self.measure_mode:
            if self.measuring and self.measure_line:
                self.measure_line[1] = self.measure_end(event)
            self.update()
            return
        index = self.window_at(self.end)
        self.hover_rect = self.windows[index][1] if index is not None else None
        self.update()

    def measure_end(self, event):
        point = event.position()
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            from .ruler import snap_axis
            start = self.measure_line[0]
            return QPointF(*snap_axis(start.x(), start.y(), point.x(), point.y()))
        return QPointF(point)

    def measurement(self):
        """Aktuelle Messung in Bildpixeln oder None."""
        if not self.measure_line:
            return None
        from .ruler import measurement
        a, b = self.pixel(self.measure_line[0]), self.pixel(self.measure_line[1])
        return measurement(a.x(), a.y(), b.x(), b.y())

    def set_measure_mode(self, enabled):
        self.measure_mode = bool(enabled)
        self.start = None
        self.measuring = False
        if not enabled:
            self.measure_line = None
        self.update()

    def mouseReleaseEvent(self, event):
        if self.measure_mode:
            if self.measuring and event.button() == Qt.MouseButton.LeftButton and self.measure_line:
                self.measure_line[1] = self.measure_end(event)
            self.measuring = False
            self.update()
            return
        if self.done or self.start is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self.end = event.position().toPoint()
        rect = QRect(self.start, self.end).normalized().intersected(self.rect())
        if rect.width() < 5 and rect.height() < 5 and self.windows:
            index = self.window_at(self.end)
            if index is not None and index < len(self.window_handles):
                self.done = True
                self._release_escape_and_focus()
                self.windowSelected.emit(self.window_handles[index])
                self.close()
                return
            if index is not None:
                content = self.windows[index][1]
                top_left, bottom_right = self.to_view(QPointF(content.topLeft())), self.to_view(QPointF(content.bottomRight() + QPoint(1, 1)))
                rect = QRect(top_left.toPoint(), bottom_right.toPoint())
        if rect.width() < 2 or rect.height() < 2:
            self.start = None
            self.update()
            return
        top_left, bottom_right = self.pixel(QPointF(rect.topLeft())), self.pixel(QPointF(rect.topLeft() + QPoint(rect.width(), rect.height())))
        box = (round(top_left.x()), round(top_left.y()), round(bottom_right.x()), round(bottom_right.y()))
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.precise_box = box
            self.update()
            return
        self.finish_box(box)

    def finish_box(self, box):
        if self.done:
            return
        self.done = True
        self._release_escape_and_focus()
        self.regionSelected.emit(box)
        self.selected.emit(self.image.crop(box))
        self.close()

    def mouseDoubleClickEvent(self, event):
        if self.done or self.measure_mode:
            return
        self.done = True
        self._release_escape_and_focus()
        self.regionSelected.emit((0, 0, self.image.width, self.image.height))
        self.selected.emit(self.image)
        self.close()

    # --- keyboard ---------------------------------------------------------------------
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()
        elif event.key() == Qt.Key.Key_L and not self.precise_box and not self.pick_color:
            self.set_measure_mode(not self.measure_mode)
        elif event.key() == Qt.Key.Key_C and event.modifiers() & Qt.KeyboardModifier.ControlModifier and self.measure_mode:
            values = self.measurement()
            if values:
                from PySide6.QtWidgets import QApplication
                from .ruler import clipboard_text
                QApplication.clipboard().setText(clipboard_text(values))
        elif event.key() == Qt.Key.Key_H and not self.precise_box:
            self.show_help = not self.show_help
            self.update()
        elif event.key() == Qt.Key.Key_M and not self.precise_box:
            self.show_cursor = not self.show_cursor
            self.refresh_cursor_layer()
            self.cursorToggled.emit(self.show_cursor)
            self.update()
        elif not self.precise_box and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            dx, dy = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}[event.key()]
            self.end = QPoint(max(0, min(self.width() - 1, self.end.x() + dx)), max(0, min(self.height() - 1, self.end.y() + dy)))
            self.update()
        elif self.precise_box:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.finish_box(self.precise_box)
                return
            direction = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
            if event.key() not in direction:
                return
            dx, dy = direction[event.key()]
            left, top, right, bottom = self.precise_box
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                dx, dy = max(-left, min(self.image.width - right, dx)), max(-top, min(self.image.height - bottom, dy))
                left, right, top, bottom = left + dx, right + dx, top + dy, bottom + dy
            else:
                right = max(left + 2, min(self.image.width, right + dx))
                bottom = max(top + 2, min(self.image.height, bottom + dy))
            self.precise_box = left, top, right, bottom
            sx, sy = self.scale()
            self.start = self.to_view(QPointF(left / sx, top / sy)).toPoint()
            self.end = self.to_view(QPointF((right - 1) / sx, (bottom - 1) / sy)).toPoint()
            self.update()

    def cancel(self):
        if self.done:
            return
        self.done = True
        self.start = None
        self.precise_box = None
        self._release_escape_and_focus()
        self.cancelled.emit()
        self.close()

    def closeEvent(self, event):
        self._release_escape_and_focus()
        if not self.done:
            self.done = True
            self.cancelled.emit()
        super().closeEvent(event)
