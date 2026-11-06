"""Adjust the recording area before the recording starts: move it, drag its edges and corners, pick a standard
size, then press "Aufnahme starten". The handles are only shown before recording; nothing of this is in the video."""
from PySide6.QtCore import Qt, QPoint, QPointF, QRect, QRectF, QTimer, Signal
from .i18n import N_, tr
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget
from .overlay import _force_win32_foreground, _is_real_win32_display

HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
SIZES = [(N_("Frei"), None), ("1920 × 1080", (1920, 1080)), ("1280 × 720", (1280, 720)), ("854 × 480", (854, 480)), ("640 × 360", (640, 360))]


def handle_points(rect):
    x, y, w, h = rect
    cx, cy = x + w / 2, y + h / 2
    return {"nw": (x, y), "n": (cx, y), "ne": (x + w, y), "e": (x + w, cy),
            "se": (x + w, y + h), "s": (cx, y + h), "sw": (x, y + h), "w": (x, cy)}


def hit_test(rect, point, margin=12):
    """"nw"/"n"/… for a handle under the point, "move" inside the rectangle, otherwise None."""
    for name, (hx, hy) in handle_points(rect).items():
        if abs(point[0] - hx) <= margin and abs(point[1] - hy) <= margin:
            return name
    x, y, w, h = rect
    return "move" if x <= point[0] <= x + w and y <= point[1] <= y + h else None


def adjust_rect(rect, mode, dx, dy, bounds, min_size=64):
    """New [x, y, w, h] after dragging by (dx, dy). The result stays inside bounds, never gets smaller than
    min_size and has even width and height (H.264)."""
    x, y, w, h = rect
    bx, by, bw, bh = bounds
    left, top, right, bottom = x, y, x + w, y + h
    if mode == "move":
        dx = max(bx - left, min(bx + bw - right, dx))
        dy = max(by - top, min(by + bh - bottom, dy))
        left, right, top, bottom = left + dx, right + dx, top + dy, bottom + dy
    else:
        if "w" in mode:
            left = max(bx, min(right - min_size, left + dx))
        if "e" in mode:
            right = min(bx + bw, max(left + min_size, right + dx))
        if "n" in mode:
            top = max(by, min(bottom - min_size, top + dy))
        if "s" in mode:
            bottom = min(by + bh, max(top + min_size, bottom + dy))
    width, height = int(right - left) // 2 * 2, int(bottom - top) // 2 * 2
    return [int(left), int(top), width, height]


def centered(rect, size, bounds):
    """The rectangle resized to a standard size around its centre, shrunk and shifted to fit the bounds."""
    bx, by, bw, bh = bounds
    width, height = min(size[0], bw) // 2 * 2, min(size[1], bh) // 2 * 2
    x = int(rect[0] + rect[2] / 2 - width / 2)
    y = int(rect[1] + rect[3] / 2 - height / 2)
    return [max(bx, min(bx + bw - width, x)), max(by, min(by + bh - height, y)), width, height]


class RegionAdjuster(QWidget):
    started = Signal(list)         # region [x, y, w, h] in native pixels
    cancelled = Signal()
    ESC_HOTKEY_ID = 0x505A

    def __init__(self, region, scale=1.0, geometry=None):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.scale = scale or 1.0
        screen_geometry = geometry or QGuiApplication.primaryScreen().virtualGeometry()
        self.setGeometry(screen_geometry)
        self.bounds = [round(screen_geometry.x() * self.scale), round(screen_geometry.y() * self.scale),
                       round(screen_geometry.width() * self.scale), round(screen_geometry.height() * self.scale)]
        self.region = list(region)
        self.drag = None
        self.finished = False
        self._esc_hotkey_registered = False
        self._esc_was_down = False
        self._esc_poll_timer = QTimer(self)
        self._esc_poll_timer.setInterval(25)
        self._esc_poll_timer.timeout.connect(self._poll_escape_key)
        self.esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.cancel)
        self.esc_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.size_box = QComboBox(self)
        for label, value in SIZES:
            self.size_box.addItem(tr(label), value)
        self.size_box.activated.connect(self.size_chosen)
        self.size_label = QLabel(self)
        self.start_button = QPushButton(tr("● Aufnahme starten"), self)
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_recording)
        self.cancel_button = QPushButton(tr("Abbrechen"), self)
        self.cancel_button.clicked.connect(self.cancel)
        self.place_controls()

    def showEvent(self, event):
        super().showEvent(event)
        self._acquire_escape_and_focus()
        QTimer.singleShot(0, self._acquire_escape_and_focus)

    def hideEvent(self, event):
        self._release_escape_and_focus()
        super().hideEvent(event)

    def _acquire_escape_and_focus(self):
        if self.finished or not self.isVisible():
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

    def _poll_escape_key(self):
        if self.finished or not _is_real_win32_display():
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

    # --- geometry helpers -------------------------------------------------------------
    def view_rect(self):
        """The region in widget (logical) coordinates."""
        x, y, w, h = self.region
        return QRectF((x - self.bounds[0]) / self.scale, (y - self.bounds[1]) / self.scale, w / self.scale, h / self.scale)

    def to_native(self, point):
        return (point.x() * self.scale + self.bounds[0], point.y() * self.scale + self.bounds[1])

    def place_controls(self):
        rect = self.view_rect()
        below = rect.bottom() + 46 < self.height()
        top = int(rect.bottom() + 8) if below else int(max(8, rect.top() - 40))
        self.size_label.setText(f"{self.region[2]} × {self.region[3]} px")
        for widget in (self.size_label, self.size_box, self.cancel_button, self.start_button):
            widget.adjustSize()
        left = max(8, min(int(rect.left()), self.width() - 560))
        for widget in (self.size_label, self.size_box, self.cancel_button, self.start_button):
            widget.move(left, top)
            left += widget.width() + 8
        self.update()

    def size_chosen(self, index):
        size = self.size_box.itemData(index)
        if size:
            self.region = centered(self.region, size, self.bounds)
            self.place_controls()

    # --- painting ---------------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))
        rect = self.view_rect()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillRect(rect, Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setPen(QPen(QColor("#e5332a"), 2))
        painter.drawRect(rect)
        painter.setBrush(QColor("#e5332a"))
        for hx, hy in handle_points((rect.x(), rect.y(), rect.width(), rect.height())).values():
            painter.drawRect(QRectF(hx - 5, hy - 5, 10, 10))

    # --- mouse ------------------------------------------------------------------------
    def mousePressEvent(self, event):
        if self.finished:
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.cancel()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        rect = self.view_rect()
        mode = hit_test((rect.x(), rect.y(), rect.width(), rect.height()), (event.position().x(), event.position().y()))
        if mode:
            self.drag = (mode, event.position(), list(self.region))

    def mouseMoveEvent(self, event):
        if self.finished:
            return
        rect = self.view_rect()
        if self.drag is None:
            mode = hit_test((rect.x(), rect.y(), rect.width(), rect.height()), (event.position().x(), event.position().y()))
            cursors = {"move": Qt.CursorShape.SizeAllCursor, "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
                       "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor, "nw": Qt.CursorShape.SizeFDiagCursor,
                       "se": Qt.CursorShape.SizeFDiagCursor, "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor}
            self.setCursor(cursors.get(mode, Qt.CursorShape.ArrowCursor))
            return
        mode, origin, start = self.drag
        delta = event.position() - origin
        self.region = adjust_rect(start, mode, round(delta.x() * self.scale), round(delta.y() * self.scale), self.bounds)
        self.size_box.setCurrentIndex(0)
        self.place_controls()

    def mouseReleaseEvent(self, event):
        self.drag = None

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.start_recording()

    # --- results ----------------------------------------------------------------------
    def start_recording(self):
        if self.finished:
            return
        self.finished = True
        self._release_escape_and_focus()
        self.started.emit(list(self.region))
        self.close()

    def cancel(self):
        if self.finished:
            return
        self.finished = True
        self.drag = None
        self._release_escape_and_focus()
        self.cancelled.emit()
        self.close()

    def closeEvent(self, event):
        self._release_escape_and_focus()
        if not self.finished:
            self.finished = True
            self.cancelled.emit()
        super().closeEvent(event)
