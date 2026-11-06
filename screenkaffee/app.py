import logging
import json
import os
from pathlib import Path
import sys
import traceback
import threading
import numpy as np
from PIL import Image
from PySide6.QtCore import Qt, QSize, QTimer, QObject, QThread, Signal, Slot, QUrl, QRect, QPointF
from PySide6.QtGui import QAction, QIcon, QPixmap, QColor, QPainter, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QSpinBox, QLineEdit, QListWidget, QListWidgetItem,
    QAbstractItemView, QSplitter, QFileDialog, QMessageBox, QInputDialog,
    QSystemTrayIcon, QMenu, QDialog, QTextEdit, QDialogButtonBox, QCheckBox, QToolButton)
from .workspace import Workspace
from .color import import_srgb, save_png, save_image
from .editor import EditorWindow, pil_to_qimage
from .capture_options import DEFAULTS as CAPTURE_DEFAULTS, EXPORT_FORMATS as CAPTURE_FORMATS, post_process, title_for, validate as validate_capture_options
from .overlay import SelectionOverlay
from .hotkeys import DEFAULT_HOTKEYS, ACTIONS
from .integrations import tool, omarchy_capture, VideoRecorder, android_capture, AndroidRecorder
from .app_widgets import *  # noqa: F401,F403
from .app_widgets import _arrow_url  # noqa: F401


def monitor_label(index, screen, primary=None):
    """'Monitor 1 · 2560×1440 (Hauptbildschirm)' in physical pixels instead of raw Windows device names."""
    size = screen.geometry().size() * screen.devicePixelRatio()
    label = f"Monitor {index + 1} · {round(size.width())}×{round(size.height())}"
    return label + " (Hauptbildschirm)" if primary is not None and screen == primary else label


class MainWindow(QMainWindow):
    def open_log(self):
        """Open screenkaffee.log (errors and warnings); explain if nothing has been logged yet."""
        log = self.workspace.root / "screenkaffee.log"
        if log.is_file() and log.stat().st_size:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(log)))
        else:
            QMessageBox.information(self, "Fehlerprotokoll", "Es wurden noch keine Fehler protokolliert.")

    def __init__(self, workspace=None):
        super().__init__()
        self.workspace = workspace or Workspace()
        self.settings_path = self.workspace.root / "settings.json"
        self.settings = {"tone_mapping": "neutral", "auto_copy": True, "auto_edit": True,
                         "minimize_to_tray": True, "microphone": "", "capture_cursor": False,
                         "retention_count": 0, "retention_days": 0, "workspaces": [],
                         "hotkeys": dict(DEFAULT_HOTKEYS), **CAPTURE_DEFAULTS}
        if self.settings_path.exists():
            try:
                self.settings.update(json.loads(self.settings_path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                pass
        from .policy import load_policy, apply_policy
        if not self.settings.get("tray_behavior_v2"):
            self.settings["minimize_to_tray"] = True
            self.settings["tray_behavior_v2"] = True
        self.policy = load_policy()
        apply_policy(self.settings, self.policy)
        from . import __version__
        self.setWindowTitle(f"screenkaffee {__version__} · Dein Screenshot-Studio")
        self.version_button = QToolButton()
        self.version_button.setObjectName("versionButton")
        self.version_button.setAutoRaise(True)
        self.version_button.setText(f"Version {__version__}")
        self.version_button.setToolTip("Installierte Version – klicken, um nach Updates zu suchen")
        self.version_button.clicked.connect(lambda: self.check_updates(manual=True))
        self.statusBar().addPermanentWidget(self.version_button)
        self.setWindowIcon(app_icon())
        self.resize(1350, 880)
        self.setMinimumSize(980, 650)
        self.setAcceptDrops(True)
        self.editors = []
        self.overlays = []
        self.tasks = []
        self.capture_pending = False
        self._capture_cancelled = False
        self._capture_token = 0
        self._esc_capture_was_down = False
        self._esc_capture_poll_timer = QTimer(self)
        self._esc_capture_poll_timer.setInterval(25)
        self._esc_capture_poll_timer.timeout.connect(self._poll_capture_escape)
        self.scroll_cancel = threading.Event()
        self.manual_scroll = None
        self.recorder = VideoRecorder()
        self.camera_overlay = None
        self.pending_video_options = None
        self.video_zoom_factor = 1
        self.recording_busy = False
        self.exit_requested = False
        self.timelapse = None
        self.step_session = None
        self.live_draw = None
        self.timelapse_timer = QTimer(self)
        self.timelapse_timer.timeout.connect(self.check_timelapse)
        self.record_timer = QTimer(self)
        self.record_timer.timeout.connect(self.check_recording)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        from .icons import header_icon
        header = QWidget()
        header.setObjectName("topHeader")
        header_vbox = QVBoxLayout(header)
        header_vbox.setContentsMargins(10, 6, 10, 6)
        header_vbox.setSpacing(5)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.workspaces_toggle = QToolButton()
        self.workspaces_toggle.setObjectName("headerIcon")
        self.workspaces_toggle.setIcon(header_icon("workspaces"))
        self.workspaces_toggle.setIconSize(QSize(20, 20))
        self.workspaces_toggle.setText("Arbeitsbereiche")
        self.workspaces_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.workspaces_toggle.setCheckable(True)
        self.workspaces_toggle.setChecked(True)
        self.workspaces_toggle.setToolTip("Sammlungen / Arbeitsbereiche ein-/ausblenden (F10)")
        self.workspaces_toggle.toggled.connect(self.toggle_collections_panel)
        top.addWidget(self.workspaces_toggle)
        capture_actions = self.command_group(top)
        self.capture_button = self.add_button(capture_actions, "+ Bereich aufnehmen", lambda: self.capture("region"), primary=True)
        self.add_button(capture_actions, "Bildschirm", lambda: self.capture("screen"))
        self.add_button(capture_actions, "Fenster", lambda: self.capture("window"))
        video_actions = self.command_group(top)
        self.video_button = self.add_button(video_actions, "● Video", self.toggle_video)
        self.video_button.setToolTip("Videoaufnahme starten / stoppen (SDR)")
        self.video_more_button = QPushButton("▾")
        self.video_more_button.setObjectName("menuArrow")
        self.video_more_button.setToolTip("Zeitraffer, Android-Video, Videos importieren und zusammenfügen")
        self.video_more_button.setMenu(QMenu(self))
        video_actions.addWidget(self.video_more_button)
        self.screen_combo = QComboBox()
        for index, screen in enumerate(QApplication.screens()):
            self.screen_combo.addItem(monitor_label(index, screen, QApplication.primaryScreen()), screen.name())
            self.screen_combo.setItemData(index, screen.name(), Qt.ItemDataRole.ToolTipRole)
        self.screen_combo.setMinimumWidth(145)
        top.addWidget(self.screen_combo)
        self.delay = QSpinBox()
        self.delay.setRange(0, 30)
        self.delay.setPrefix("Verzögerung ")
        self.delay.setSuffix(" s")
        self.delay.setToolTip("Aufnahmeverzögerung in Sekunden")
        top.addWidget(self.delay)
        top.addStretch()
        brand = QLabel("screenkaffee")
        brand.setObjectName("brand")
        top.addWidget(brand)
        import_btn = QToolButton()
        import_btn.setObjectName("headerIcon")
        import_btn.setIcon(header_icon("import"))
        import_btn.setIconSize(QSize(18, 18))
        import_btn.setText("Importieren …")
        import_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        import_btn.clicked.connect(self.import_files)
        top.addWidget(import_btn)
        settings_btn = QToolButton()
        settings_btn.setObjectName("headerIcon")
        settings_btn.setIcon(header_icon("settings"))
        settings_btn.setIconSize(QSize(18, 18))
        settings_btn.setText("Einstellungen")
        settings_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        settings_btn.clicked.connect(self.preferences)
        top.addWidget(settings_btn)
        header_vbox.addLayout(top)
        filterbar = QHBoxLayout()
        filterbar.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Aufnahmen filtern (einfach tippen, Strg+F oder Esc) …")
        self.search.textChanged.connect(self.refresh)
        filterbar.addWidget(self.search, 1)
        self.collection = QComboBox(root)
        self.collection.addItem("Alle Aufnahmen")
        self.collection.currentTextChanged.connect(self.refresh)
        self.collection.currentTextChanged.connect(self.sync_collection_list)
        self.collection.hide()
        self.type_filter = QComboBox()
        for label, value in [("Alle Medien", ""), ("Bilder", "image"), ("Videos", "video"), ("Dokumente", "document")]:
            self.type_filter.addItem(label, value)
        self.type_filter.currentIndexChanged.connect(self.refresh)
        filterbar.addWidget(self.type_filter)
        self.sort_order = QComboBox()
        for label, value in [("Neueste zuerst", "newest"), ("Älteste zuerst", "oldest"), ("Titel", "title"), ("Medientyp", "type"), ("Bildgröße", "size")]:
            self.sort_order.addItem(label, value)
        self.sort_order.currentIndexChanged.connect(self.refresh)
        filterbar.addWidget(self.sort_order)
        self.time_filter = QComboBox()
        for label, value in [("Jederzeit", 0), ("Letzte 24 Stunden", 1), ("Letzte 7 Tage", 7), ("Letzte 30 Tage", 30)]:
            self.time_filter.addItem(label, value)
        self.time_filter.currentIndexChanged.connect(self.refresh)
        filterbar.addWidget(self.time_filter)
        self.count_label = QLabel()
        self.count_label.setObjectName("muted")
        filterbar.addWidget(self.count_label)
        header_vbox.addLayout(filterbar)
        layout.addWidget(header)
        bottom_bar = QWidget()
        bottom_bar.setObjectName("bottomBar")
        tools_row = QHBoxLayout(bottom_bar)
        tools_row.setContentsMargins(14, 6, 14, 5)
        tools_row.setSpacing(14)
        tools = tools_row
        self.media_buttons = []
        for label, glyph, color, callback in [("Aufnehmen", "camera", "#e5332a", lambda: self.capture("region")),
            ("Bearbeiten", "edit", "#4caf50", self.edit_selected), ("Kopieren", "copy", "#2c82c9", self.copy_selected),
            ("Exportieren", "export", "#2c82c9", self.export_selected)]:
            button = self.round_button(tools, label, glyph, color, callback)
            if label != "Aufnehmen":
                self.media_buttons.append(button)
            if label == "Bearbeiten":
                self.edit_button = button
        share = self.round_button(tools, "Teilen", "share", "#2c82c9", None)
        self.share_button = share
        share.setToolTip("Aufs Handy senden, schicker Screenshot, drucken, E-Mail")
        share_popup = QMenu(self)
        share.setMenu(share_popup)
        share.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.media_buttons.append(share)
        self.media_buttons.append(self.round_button(tools, "Dokument", "document", "#2c82c9", self.document_export))
        self.trash_buttons = [self.round_button(tools, label, glyph, "#808080", callback) for label, glyph, callback in [
            ("Wiederherstellen", "restore", self.restore_trash_selected),
            ("Endgültig löschen", "trash", self.delete_trash_selected),
            ("Papierkorb leeren", "trash", self.empty_trash_view)]]
        menu = QMenu(self)
        self.timelapse_action = QAction("Zeitraffer starten …", self)
        self.timelapse_action.triggered.connect(lambda _=False: self.toggle_timelapse())
        self.android_video_action = QAction("Android-Video starten", self)
        self.android_video_action.triggered.connect(lambda _=False: self.android_video())
        self.step_guide_action = QAction("Schritt-Anleitung aufnehmen …", self)
        self.step_guide_action.setToolTip("Bei jedem Mausklick ein Bild mit markierter Klickstelle – daraus wird ein Dokument")
        self.step_guide_action.triggered.connect(lambda _=False: self.toggle_step_guide())
        self.live_draw_action = QAction("Live-Zeichnen auf dem Bildschirm", self)
        self.live_draw_action.triggered.connect(lambda _=False: self.toggle_live_draw())
        self.measure_action = QAction("Bildschirm ausmessen (Lineal)", self)
        self.measure_action.triggered.connect(lambda _=False: self.capture("measure"))
        capture_menu = menu.addMenu("Aufnehmen")
        capture_menu.addAction("Vorherigen Bereich erneut aufnehmen", lambda: self.capture("previous"))
        capture_menu.addAction("Alle Bildschirme aufnehmen", lambda: self.capture("all"))
        capture_menu.addAction("Webseite komplett aufnehmen …", self.webpage)
        capture_menu.addAction("Farbpipette", lambda: self.capture("color"))
        capture_menu.addAction("Android-Screenshot", self.android)
        capture_menu.addSeparator()
        capture_menu.addAction(self.step_guide_action)
        capture_menu.addAction(self.measure_action)
        capture_menu.addSeparator()
        capture_menu.addAction("Automatische Scrollaufnahme …", lambda: self.capture("scroll"))
        capture_menu.addAction("Manuelle Scrollserie starten …", lambda: self.capture("manual-scroll"))
        capture_menu.addAction("Scrollserie: nächstes Bild (Strg+Alt+F7)", self.manual_scroll_frame)
        capture_menu.addAction("Scrollserie: fertigstellen (Strg+Alt+F8)", self.finish_manual_scroll)
        capture_menu.addSeparator()
        capture_menu.addAction("Aus Zwischenablage importieren", self.paste_image)
        self.fill_video_menu(menu.addMenu("Video"))
        selection_menu = menu.addMenu("Auswahl bearbeiten")
        selection_menu.addAction("Umbenennen / Tags / Sammlung (F2)", self.edit_details)
        selection_menu.addAction("Schnellansicht (Leertaste)", self.quick_view_selected)
        selection_menu.addAction("Medieneigenschaften anzeigen", self.show_media_properties)
        selection_menu.addAction("Auswahl im Dateimanager zeigen", self.reveal_selected)
        selection_menu.addSeparator()
        selection_menu.addAction("Zwei Aufnahmen vergleichen …", self.compare_selected)
        selection_menu.addAction("Scrollbilder verbinden", self.stitch_selected)
        selection_menu.addAction("QR-Code lesen", self.qr_selected)
        selection_menu.addSeparator()
        selection_menu.addAction("Auto-Umbenennen (screenshot_1, …)", self.renumber_selected)
        selection_menu.addAction("Neu komprimieren (verlustfrei)", self.recompress_selected)
        share_menu = menu.addMenu("Teilen und ausgeben")
        self.fill_share_menu(share_menu)
        share_menu.addSeparator()
        share_menu.addAction("Stapelexport mit Größe und Effekten …", self.batch_export_selected)
        data_menu = menu.addMenu("Sammlungen und Daten")
        data_menu.addAction("Neue Sammlung …", self.new_collection)
        data_menu.addAction("Sammlungsleiste ein-/ausblenden (F10)", self.workspaces_toggle.toggle)
        data_menu.addAction("Vorschau-Seitenleiste ein-/ausblenden", self.toggle_inspector_panel)
        data_menu.addSeparator()
        data_menu.addAction("Arbeitsbereich wechseln …", self.switch_workspace)
        data_menu.addAction("Mit lokalem Ordner abgleichen …", self.sync_local_folder)
        data_menu.addAction("Aufbewahrungsregeln jetzt anwenden …", lambda: self.apply_retention(confirm=True))
        data_menu.addAction("Gelöschte Aufnahmen wiederherstellen …", self.restore_deleted)
        data_menu.addAction("Aufräumen (doppelte Bilder, große Videos) …", self.cleanup_library)
        data_menu.addSeparator()
        data_menu.addAction("Einstellungen exportieren …", self.export_settings)
        data_menu.addAction("Einstellungen importieren …", self.import_settings)
        data_menu.addAction("Datenordner öffnen", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.workspace.root))))
        help_menu = menu.addMenu("Hilfe und Updates")
        help_menu.addAction("Befehl suchen … (Strg+K)", self.open_command_palette)
        help_menu.addAction("Tour: die besten Funktionen …", self.start_tour)
        help_menu.addSeparator()
        help_menu.addAction("Nach Updates suchen …", lambda: self.check_updates(manual=True))
        help_menu.addAction("Vorige Version wiederherstellen …", self.restore_previous_version)
        help_menu.addAction("Einrichtung (Taste, Autostart, Updates) …", self.first_run_setup)
        help_menu.addSeparator()
        help_menu.addAction("Fehlerprotokoll öffnen", self.open_log)
        help_menu.addAction("Funktionsstand / Hinweise", self.about)
        menu.addSeparator()
        menu.addAction("Auswahl entfernen …", self.remove_selected)
        self.fill_video_menu(self.video_more_button.menu())
        self.fill_share_menu(share_popup)
        self.more_menu = menu
        palette_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        palette_shortcut.activated.connect(self.open_command_palette)
        more = self.round_button(tools, "Mehr", "more", "#808080", None)
        self.more_button = more
        more.setMenu(menu)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tools_row.addStretch()
        self.round_button(tools, "Schließen", "close", "#e8912d", self.close)
        splitter = QSplitter()
        self.list = CaptureList(self)
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setIconSize(QSize(172, 118))
        self.list.setGridSize(QSize(196, 152))
        self.list.setWordWrap(True)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemDoubleClicked.connect(lambda item: self.edit_selected())
        self.list.itemSelectionChanged.connect(self.show_preview)
        self.collection_list = CollectionList()
        self.collection_list.mediaDropped.connect(self.move_media_to_collection)
        self.collection_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.collection_list.customContextMenuRequested.connect(self.collection_menu)
        for keys, step in (("Ctrl+Up", -1), ("Ctrl+Down", 1)):
            shortcut = QShortcut(QKeySequence(keys), self.collection_list)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(lambda step=step: self.shift_collection(self.collection.currentText(), step))
        rename = QShortcut(QKeySequence("F2"), self.collection_list)
        rename.setContext(Qt.ShortcutContext.WidgetShortcut)
        rename.activated.connect(lambda: self.rename_collection(self.collection.currentText()))
        self.collection_list.setObjectName("collections")
        self.collection_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.collection_list.setFlow(QListWidget.Flow.TopToBottom)
        self.collection_list.setWrapping(False)
        self.collection_list.setMovement(QListWidget.Movement.Static)
        self.collection_list.setIconSize(QSize(32, 32))
        self.collection_list.setFixedWidth(116)
        self.collection_list.setWordWrap(True)
        self.collection_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.collection_list.setGridSize(QSize(110, 74))
        self.collection_list.currentTextChanged.connect(self.collection_chosen)
        splitter.addWidget(self.collection_list)
        splitter.addWidget(self.list)
        side = QWidget()
        side.setObjectName("inspector")
        side.setMaximumWidth(320)
        sidelay = QVBoxLayout(side)
        sidelay.setContentsMargins(18, 20, 18, 20)
        heading = QLabel("VORSCHAU & DETAILS")
        heading.setObjectName("eyebrow")
        sidelay.addWidget(heading)
        self.preview = QLabel("Dein nächster guter Screenshot\nbeginnt hier.")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(260, 190)
        self.preview.setWordWrap(True)
        sidelay.addWidget(self.preview)
        self.detail = QLabel("Bereich, Fenster oder Bildschirm aufnehmen.\n\nBilder lassen sich auch hierher ziehen.")
        self.detail.setWordWrap(True)
        self.detail.setObjectName("muted")
        sidelay.addWidget(self.detail)
        sidelay.addStretch()
        platform = "Windows · FP16-Aufnahme → sRGB-PNG" if sys.platform == "win32" else "Linux · Compositor → sRGB-PNG\nHDR-Farben auf Omarchy noch zu validieren"
        note = QLabel(platform + "\n\nLokal gespeichert · Keine Cloud-Uploads\nDaten neben der App")
        note.setObjectName("muted")
        note.setWordWrap(True)
        sidelay.addWidget(note)
        self.inspector_panel = side
        side.hide()
        splitter.addWidget(side)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)
        splitter.setSizes([116, 1180, 0])
        layout.addWidget(splitter, 1)
        layout.addWidget(bottom_bar)
        self.setCentralWidget(root)
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("screenkaffee")
        traymenu = QMenu(self)
        traymenu.addAction("Bereich aufnehmen", lambda: self.capture("region"))
        traymenu.addAction("Workspace öffnen", self.restore)
        traymenu.addAction("Video pausieren / fortsetzen", self.pause_video)
        traymenu.addAction("Video: hineinzoomen", lambda: self.change_video_zoom(1))
        traymenu.addAction("Video: herauszoomen", lambda: self.change_video_zoom(-1))
        traymenu.addAction("Video: Zoom zurücksetzen", lambda: self.change_video_zoom(0))
        traymenu.addAction("Video stoppen", lambda: self.toggle_video() if self.recorder.active else None)
        traymenu.addAction("Scrollaufnahme beenden", self.scroll_cancel.set)
        traymenu.addAction("Scrollserie: nächstes Bild", self.manual_scroll_frame)
        traymenu.addAction("Scrollserie: fertigstellen", self.finish_manual_scroll)
        traymenu.addAction("Zeitraffer stoppen", lambda: self.stop_timelapse() if self.timelapse else None)
        traymenu.addAction("Schritt-Anleitung beenden", lambda: self.step_session.finish() if self.step_session else None)
        traymenu.addAction("Live-Zeichnen ein / aus", self.toggle_live_draw)
        traymenu.addSeparator()
        traymenu.addAction("Beenden", self.quit_app)
        self.tray.setContextMenu(traymenu)
        self.tray.activated.connect(lambda reason: self.restore() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.shortcuts = []
        for key, callback in [("Ctrl+N", lambda: self.capture("region")), ("Ctrl+O", self.import_files),
            ("Ctrl+V", self.paste_image), ("Ctrl+E", self.edit_selected), ("Delete", self.remove_selected),
            ("F10", self.workspaces_toggle.toggle), ("Ctrl+F", lambda: (self.search.setFocus(), self.search.selectAll()))]:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)
        self.hotkeys = None
        if sys.platform == "win32":
            from .hotkeys import WindowsHotkeys
            self.hotkeys = WindowsHotkeys({kind: (lambda k=kind: QTimer.singleShot(0, lambda: self.command_action(k)))
                                           for kind, _ in ACTIONS})
            app_inst = QApplication.instance()
            app_inst.installNativeEventFilter(self.hotkeys)
            hk = self.hotkeys
            self.destroyed.connect(lambda *_: (hk.close(), app_inst.removeNativeEventFilter(hk) if QApplication.instance() else None))
            problems = self.apply_hotkeys()
            if problems:
                from shiboken6 import isValid
                QTimer.singleShot(0, lambda: self.report_hotkey_problems(problems) if isValid(self) else None)
            if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
                from .windows_capture import warmup_capture
                QTimer.singleShot(50, warmup_capture)
        self.refresh_collections()
        self.refresh()
        self.pending_update = None
        self.update_busy = False
        from shiboken6 import isValid
        QTimer.singleShot(0, lambda: self.startup_update() if isValid(self) else None)
        if self.hotkeys and self.hotkeys.registered:
            keys = ", ".join(f"{self.settings['hotkeys'][k]}: {label}" for k, label in ACTIONS if self.settings["hotkeys"].get(k))
            self.statusBar().showMessage("Bereit · " + keys)
        else:
            self.statusBar().showMessage("Bereit · Strg+N: Aufnahme · Tastenkürzel unter Einstellungen festlegen")

    def fill_video_menu(self, menu):
        """Gemeinsamer Inhalt für „Mehr → Video“ und das Aufklappmenü am Video-Knopf."""
        menu.addAction("Videoaufnahme starten / stoppen …", self.toggle_video)
        menu.addAction(self.timelapse_action)
        menu.addAction(self.android_video_action)
        menu.addAction(self.live_draw_action)
        menu.addSeparator()
        menu.addAction("Video pausieren / fortsetzen", self.pause_video)
        menu.addAction("Video: hineinzoomen (Strg+Alt+↑)", lambda: self.change_video_zoom(1))
        menu.addAction("Video: herauszoomen (Strg+Alt+↓)", lambda: self.change_video_zoom(-1))
        menu.addAction("Video: Zoom zurücksetzen (Strg+Alt+Pos1)", lambda: self.change_video_zoom(0))
        menu.addSeparator()
        menu.addAction("Videos importieren …", self.import_videos)
        menu.addAction("Ausgewählte Videos zusammenfügen …", self.merge_selected_videos)

    def fill_share_menu(self, menu):
        """Gemeinsamer Inhalt für den Knopf „Teilen“ und „Mehr → Teilen und ausgeben“."""
        menu.addAction("Aufs Handy senden (QR-Code) …", self.share_to_phone)
        menu.addAction("Schicker Screenshot (Hintergrund, Rahmen) …", self.beautify_selected)
        menu.addSeparator()
        menu.addAction("In Zwischenablage kopieren", self.copy_selected)
        menu.addAction("Drucken …", self.print_selected)
        menu.addAction("E-Mail-Entwurf speichern …", self.mail_draft)

    def toggle_collections_panel(self, checked=None):
        visible = not self.collection_list.isVisible() if checked is None else bool(checked)
        self.collection_list.setVisible(visible)
        if self.workspaces_toggle.isChecked() != visible:
            self.workspaces_toggle.blockSignals(True)
            self.workspaces_toggle.setChecked(visible)
            self.workspaces_toggle.blockSignals(False)

    def toggle_inspector_panel(self):
        self.inspector_panel.setVisible(not self.inspector_panel.isVisible())

    def show_capture_context_menu(self, global_pos):
        menu = QMenu(self)
        if self.in_trash():
            menu.addAction("Wiederherstellen", self.restore_trash_selected)
            menu.addAction("Endgültig löschen …", self.delete_trash_selected)
            menu.addAction("Papierkorb leeren …", self.empty_trash_view)
            menu.exec(global_pos)
            return
        ids = self.selected_ids()
        if ids:
            menu.addAction("Bearbeiten (Strg+E)", self.edit_selected)
            menu.addAction("In externem Programm öffnen", self.open_in_external_editor)
            menu.addAction("Schnellansicht (Leertaste)", self.quick_view_selected)
            menu.addAction("Anheften (immer im Vordergrund)", self.pin_selected)
            menu.addAction("Schicker Screenshot (Hintergrund, Rahmen) …", self.beautify_selected)
            if len(ids) == 2:
                menu.addAction("Vergleichen (vorher ↔ nachher)", self.compare_selected)
            menu.addAction("Aufs Handy senden (QR-Code) …", self.share_to_phone)
            menu.addSeparator()
            menu.addAction("In Zwischenablage kopieren", self.copy_selected)
            menu.addAction("Exportieren / Speichern unter …", self.export_selected)
            menu.addAction("Stapelexport mit Größe und Effekten …", self.batch_export_selected)
            menu.addAction("Dokument erstellen …", self.document_export)
            menu.addAction("Scrollbilder verbinden", self.stitch_selected)
            menu.addAction("Drucken …", self.print_selected)
            menu.addAction("E-Mail-Entwurf speichern …", self.mail_draft)
            menu.addSeparator()
            all_fav = all(self.workspace.get(i)["favorite"] for i in ids)
            menu.addAction("★ Favorit entfernen" if all_fav else "★ Als Favorit markieren", self.toggle_favorite_selected)
            menu.addAction("Umbenennen / Tags / Sammlung (F2)", self.edit_details)
            menu.addAction("Auto-Umbenennen (screenshot_1, …)", self.renumber_selected)
            menu.addAction("Neu komprimieren (verlustfrei)", self.recompress_selected)
            menu.addAction("Medieneigenschaften anzeigen", self.show_media_properties)
            menu.addAction("Im Dateimanager zeigen", self.reveal_selected)
            menu.addSeparator()
            menu.addAction("Auswahl entfernen (Entf)", self.remove_selected)
        else:
            menu.addAction("Aus Zwischenablage importieren (Strg+V)", self.paste_image)
            menu.addAction("Bilder importieren … (Strg+O)", self.import_files)
            menu.addAction("Videos importieren …", self.import_videos)
            menu.addAction("Gelöschte Aufnahmen wiederherstellen …", self.restore_deleted)
            menu.addAction("Datenordner öffnen", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.workspace.root))))
        menu.exec(global_pos)

    def open_in_external_editor(self):
        ident = self.require_one()
        if ident:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.workspace.path(ident).resolve())))

    def export_project_bundle(self, ident, folder=None):
        """Export a portable .screenkaffee.json project bundle (with embedded base image and vector annotations)."""
        import base64
        import io
        folder = Path(folder or (self.workspace.root / "drag-exports"))
        folder.mkdir(parents=True, exist_ok=True)
        row = self.workspace.get(ident)
        project_file = self.workspace.project_path(ident)
        data = json.loads(project_file.read_text(encoding="utf-8")) if project_file.exists() else {"version": 2, "effects": {}, "annotations": []}
        buf = io.BytesIO()
        self.workspace.image(ident).save(buf, format="PNG")
        bundle = dict(data, format="screenkaffee-project", title=row["title"], tags=row["tags"],
                      image_png_base64=base64.b64encode(buf.getvalue()).decode("ascii"))
        destination = folder / f"{ident}.screenkaffee.json"
        destination.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
        return destination

    def apply_hotkeys(self):
        """Register the configured global hotkeys; returns {action: problem}."""
        if not self.hotkeys:
            return {}
        bindings = dict(DEFAULT_HOTKEYS)
        bindings.update({k: v for k, v in self.settings.get("hotkeys", {}).items() if isinstance(v, str)})
        return self.hotkeys.bind(bindings)

    def report_hotkey_problems(self, problems):
        """Startup notice for hotkeys that could not be registered: which action, which key, where to change it."""
        text = "\n".join(problems.values()) + "\nÄndern unter Einstellungen → Tastenkürzel."
        self.statusBar().showMessage("Tastenkürzel nicht verfügbar – " + "; ".join(problems.values()), 30000)
        if self.tray.isVisible():
            self.tray.showMessage("screenkaffee: Tastenkürzel belegt", text, QSystemTrayIcon.MessageIcon.Warning, 10000)
        return text

    def command_action(self, action):
        if action == "scroll-frame":
            self.manual_scroll_frame()
        elif action == "scroll-finish":
            self.finish_manual_scroll()
        elif action in ("zoom-in", "zoom-out", "zoom-reset"):
            self.change_video_zoom({"zoom-in": 1, "zoom-out": -1, "zoom-reset": 0}[action])
        elif action == "video-pause":
            self.pause_video()
        elif action == "video":
            self.toggle_video()
        elif action == "timelapse":
            self.toggle_timelapse()
        elif action == "steps":
            self.toggle_step_guide()
        elif action == "draw":
            self.toggle_live_draw()
        else:
            self.capture(action)

    def change_video_zoom(self, direction):
        if not self.recorder.active or self.recording_busy:
            return
        self.video_zoom_factor = 1 if direction == 0 else min(8, max(1, self.video_zoom_factor + direction * .5))
        from PySide6.QtGui import QCursor
        pos = QCursor.pos()
        region = self.recorder.options.get("region")
        if region:
            x, y, width, height = region
        else:
            geometry = QApplication.primaryScreen().virtualGeometry()
            x, y, width, height = geometry.x(), geometry.y(), geometry.width(), geometry.height()
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            point = wintypes.POINT()
            if ctypes.windll.user32.GetPhysicalCursorPos(ctypes.byref(point)):
                pos.setX(point.x)
                pos.setY(point.y)
            if not region:
                user = ctypes.windll.user32
                x, y, width, height = (user.GetSystemMetrics(i) for i in (76, 77, 78, 79))
        focus = (min(1, max(0, (pos.x() - x) / max(1, width))), min(1, max(0, (pos.y() - y) / max(1, height))))
        self.recorder.set_zoom(self.video_zoom_factor, *focus)
        self.update_zoom_frame([x, y, width, height], focus)
        self.statusBar().showMessage(f"Video-Zoom: {self.video_zoom_factor:g}× · Ausschnitt an Mausposition · wird beim Speichern eingerechnet")

    def screen_for_region(self, region=None):
        """Return the QScreen containing the given native/logical region (or the chosen/cursor screen)."""
        screens = QApplication.screens()
        if not screens:
            return QApplication.primaryScreen()
        if region and len(region) >= 4:
            cx, cy = region[0] + region[2] / 2, region[1] + region[3] / 2
            for s in screens:
                g = s.geometry()
                dpr = s.devicePixelRatio() if sys.platform == "win32" else 1.0
                if g.x() <= cx < g.x() + g.width() * dpr and g.y() <= cy < g.y() + g.height() * dpr:
                    return s
        from PySide6.QtGui import QCursor
        from .capture_options import pick_screen
        cursor = QCursor.pos()
        wanted = pick_screen([(s.name(), (s.geometry().x(), s.geometry().y(), s.geometry().width(), s.geometry().height())) for s in screens],
                             (cursor.x(), cursor.y()), self.screen_combo.currentData(), self.settings.get("follow_mouse_screen", False))
        return next((s for s in screens if s.name() == wanted), QApplication.primaryScreen())

    def update_zoom_frame(self, region, focus):
        """Green frame and live magnified preview around the zoomed area while recording; hidden again at 1x."""
        from .videoextras import ZoomFrame, LiveZoomPreview, zoom_frame_rect
        frame = getattr(self, "zoom_frame", None)
        preview = getattr(self, "zoom_preview", None)
        if self.video_zoom_factor <= 1:
            if frame is not None:
                frame.hide()
            if preview is not None:
                preview.hide()
            return
        if frame is None:
            frame = self.zoom_frame = ZoomFrame()
        if preview is None:
            preview = self.zoom_preview = LiveZoomPreview()
        screen = self.screen_for_region(region)
        scale = screen.devicePixelRatio() if (sys.platform == "win32" and screen) else 1
        rect = zoom_frame_rect(region, self.video_zoom_factor, *focus)
        frame.show_area(rect, scale)
        preview.show_area(rect, self.video_zoom_factor, scale)

    def round_button(self, layout, label, glyph, color, callback):
        from .icons import round_icon
        button = QToolButton()
        button.setObjectName("roundAction")
        button.setIcon(round_icon(glyph, color))
        button.setIconSize(QSize(44, 44))
        button.setText(label)
        button.setToolTip(label)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        if callback:
            button.clicked.connect(lambda _=False: callback())
        layout.addWidget(button)
        return button

    def collection_menu(self, position):
        item = self.collection_list.itemAt(position)
        if item is None or item.text().startswith("+"):
            return
        name = item.text()
        if name.startswith("Papierkorb ("):
            return
        menu = QMenu(self)
        menu.addAction("Ordner im Dateimanager zeigen", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.workspace.images))))
        if name != self.workspace.DEFAULT_COLLECTION:
            menu.addAction("Umbenennen … (F2)", lambda: self.rename_collection(name))
            menu.addAction("Symbol wählen …", lambda: self.choose_collection_icon(name))
            menu.addAction("Nach oben (Strg+↑)", lambda: self.shift_collection(name, -1))
            menu.addAction("Nach unten (Strg+↓)", lambda: self.shift_collection(name, 1))
            menu.addSeparator()
            menu.addAction("Löschen …", lambda: self.delete_collection(name))
        menu.exec(self.collection_list.mapToGlobal(position))

    def rename_collection(self, name):
        if name.startswith("Papierkorb ("):
            return
        if name == self.workspace.DEFAULT_COLLECTION:
            return
        new, ok = QInputDialog.getText(self, "Sammlung umbenennen", "Neuer Name", text=name)
        if ok and new.strip() and new.strip() != name:
            try:
                self.workspace.rename_collection(name, new)
                self.refresh_collections()
                self.collection.setCurrentText(new.strip())
                self.refresh()
            except ValueError as exc:
                QMessageBox.warning(self, "Sammlung", str(exc))

    def delete_collection(self, name):
        box = QMessageBox(self)
        box.setWindowTitle("Sammlung löschen")
        box.setText(f"Sammlung „{name}“ löschen?")
        box.setInformativeText("Die Aufnahmen können in „Alle Aufnahmen“ bleiben oder ebenfalls entfernt werden (wiederherstellbar).")
        keep = box.addButton("Aufnahmen behalten", QMessageBox.ButtonRole.AcceptRole)
        drop = box.addButton("Auch Aufnahmen entfernen", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() not in (keep, drop):
            return
        try:
            self.workspace.delete_collection(name, delete_files=box.clickedButton() is drop)
        except ValueError as exc:
            QMessageBox.warning(self, "Sammlung", str(exc))
            return
        self.collection.setCurrentText(self.workspace.DEFAULT_COLLECTION)
        self.refresh_collections()
        self.refresh()

    def shift_collection(self, name, step):
        if name.startswith("Papierkorb ("):
            return
        try:
            if self.workspace.move_collection(name, step):
                self.refresh_collections()
                self.collection.setCurrentText(name)
                self.sync_collection_list(name)
        except ValueError:
            pass

    def choose_collection_icon(self, name):
        if name.startswith("Papierkorb ("):
            return
        label, ok = QInputDialog.getItem(self, "Symbol wählen", "Symbol für die Sammlung", list(COLLECTION_ICONS.values()), editable=False)
        if ok:
            glyph = next(key for key, text in COLLECTION_ICONS.items() if text == label)
            self.workspace.set_collection_icon(name, glyph)
            self.refresh_collections()

    def move_media_to_collection(self, ids, name):
        if name.startswith("Papierkorb ("):
            return
        try:
            self.workspace.move_to_collection(ids, name)
        except (KeyError, ValueError) as exc:
            self.fail(str(exc))
            return
        self.refresh()
        self.statusBar().showMessage(f"{len(ids)} Aufnahme(n) nach „{name}“ verschoben")

    def collection_chosen(self, text):
        """Sidebar click drives the hidden collection combo that all queries use."""
        if not text or text == self.collection.currentText():
            return
        if text.startswith("+"):
            self.sync_collection_list(self.collection.currentText())
            self.new_collection()
        else:
            self.collection.setCurrentText(text)

    def sync_collection_list(self, text=None):
        text = text or self.collection.currentText()
        self.collection_list.blockSignals(True)
        for index in range(self.collection_list.count()):
            if self.collection_list.item(index).text() == text:
                self.collection_list.setCurrentRow(index)
        self.collection_list.blockSignals(False)

    def add_button(self, layout, label, callback, primary=False):
        button = QPushButton(label)
        if primary:
            button.setObjectName("primary")
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def command_group(self, parent_layout):
        group = QWidget()
        group.setObjectName("commandGroup")
        buttons = QHBoxLayout(group)
        buttons.setContentsMargins(3, 3, 3, 3)
        buttons.setSpacing(0)
        parent_layout.addWidget(group, 0, Qt.AlignmentFlag.AlignVCenter)
        return buttons

    def task(self, operation, done, message):
        thread = QThread(self)
        worker = Worker(operation)
        delivery = TaskDelivery(self, done)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(delivery.success, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(delivery.failure, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        pair = (thread, worker)
        self.tasks.append(pair)
        thread.finished.connect(lambda: self.tasks.remove(pair) if pair in self.tasks else None)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(delivery.deleteLater)
        self.statusBar().showMessage(message)
        thread.start()

    def blocked(self, feature):
        """True (with a message) if policy.json disables this feature."""
        if feature in self.policy["disabled"]:
            self.statusBar().showMessage("Per Richtlinie deaktiviert: " + feature)
            QMessageBox.information(self, "Richtlinie", "Diese Funktion ist per Richtlinie deaktiviert.")
            return True
        return False

    def fail(self, message):
        if self.recording_busy:
            self.recording_busy = False
            self.video_button.setEnabled(True)
        self.capture_pending = False
        self.capture_button.setEnabled(True)
        self.restore()
        self.statusBar().showMessage("Vorgang fehlgeschlagen")
        QMessageBox.warning(self, "screenkaffee", message)

    def restore(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _start_capture_escape_watch(self):
        from .overlay import _is_real_win32_display
        if _is_real_win32_display():
            try:
                import ctypes
                self._esc_capture_was_down = bool(ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000)
                if not self._esc_capture_poll_timer.isActive():
                    self._esc_capture_poll_timer.start()
            except Exception:
                pass

    def _stop_capture_escape_watch(self):
        self._esc_capture_poll_timer.stop()

    def _poll_capture_escape(self):
        from .overlay import _is_real_win32_display
        if not _is_real_win32_display():
            return
        try:
            import ctypes
            down = bool(ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000)
            if down and not self._esc_capture_was_down:
                self.cancel_capture()
                return
            self._esc_capture_was_down = down
        except Exception:
            pass

    def cancel_capture(self):
        """Cancel any active screenshot overlay, video region adjuster, delay countdown, or scroll capture."""
        if self.overlays:
            for overlay in list(self.overlays):
                overlay.cancel()
            return True
        adjuster = getattr(self, "region_adjuster", None)
        if adjuster is not None and not getattr(adjuster, "finished", True):
            adjuster.cancel()
            return True
        if self.manual_scroll is not None:
            self.manual_scroll = None
            self.tray.setToolTip("screenkaffee")
            self._capture_token += 1
            self._capture_cancelled = True
            self._stop_capture_escape_watch()
            self.finish_capture(None, {})
            return True
        if self.capture_pending:
            self._capture_token += 1
            self._capture_cancelled = True
            self.scroll_cancel.set()
            self._stop_capture_escape_watch()
            self.finish_capture(None, {})
            return True
        return False

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.cancel_capture():
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_capture_delay_timeout(self, kind, token):
        if token != self._capture_token or self._capture_cancelled or not self.capture_pending:
            return
        self.perform_capture(kind)

    def capture(self, kind="region"):
        if self.capture_pending:
            return
        self.capture_pending = True
        self._capture_cancelled = False
        self._capture_token += 1
        token = self._capture_token
        self.capture_button.setEnabled(False)
        was_visible = self.isVisible()
        self.hide()
        QApplication.processEvents()
        self._start_capture_escape_watch()
        wait_ms = self.delay.value() * 1000 if self.delay.value() > 0 else (20 if was_visible else 0)
        QTimer.singleShot(wait_ms, lambda: self._on_capture_delay_timeout(kind, token))

    def perform_capture(self, kind):
        if self._capture_cancelled:
            return
        if kind == "color" and sys.platform != "win32" and tool("hyprpicker"):
            from .integrations import run
            def pick():
                result = run([tool("hyprpicker"), "-f", "hex"], timeout=120)
                if result.returncode:
                    return None
                import re
                match = re.search(r"#[0-9A-Fa-f]{6}", result.stdout.decode())
                if not match:
                    raise RuntimeError("Die Farbpipette lieferte keinen gültigen Farbwert.")
                return match.group().upper()
            self.task(pick, lambda value: self.color_picked(None, value) if value else self.finish_capture(None, {}), "Farbpipette …")
            return
        if kind == "previous":
            bounds = self.settings.get("last_region")
            if not bounds or self.settings.get("last_region_platform") != sys.platform:
                self.fail("Auf diesem System wurde noch kein Bereich aufgenommen.")
                return
            from .capture import capture_region
            self.task(lambda: capture_region(bounds, self.settings["tone_mapping"], self.settings["capture_cursor"]),
                lambda result: self.finish_capture(*result), "Vorherigen Bereich aufnehmen …")
            return
        if sys.platform == "win32":
            from PySide6.QtGui import QCursor
            from .windows_capture import capture_monitors
            self._capture_cursor_pos = QCursor.pos()
            target = None
            if kind != "all":
                screen = self.screen_for_region()
                if screen is not None:
                    geo = screen.geometry()
                    target = (geo.x(), geo.y())
            hw_cursor = bool(self.settings["capture_cursor"]) if kind in ("all", "screen") else False
            self.task(lambda: capture_monitors(self.settings["tone_mapping"], cursor=hw_cursor, target=target),
                      lambda result: self.windows_result(result, kind), "GPU-Aufnahme …")
        elif os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and tool("grim"):
            name = self.screen_combo.currentData()
            grim_kind = "screen" if kind == "measure" else kind
            self.task(lambda: omarchy_capture(grim_kind, self.settings["capture_cursor"], return_region=True, monitor_name=name),
                lambda result: self.omarchy_result(result, kind),
                "Omarchy-Aufnahme …")
        else:
            from .linux_capture import is_wayland, portal_screenshot
            if is_wayland():
                try:
                    image = portal_screenshot()
                    if kind == "measure":
                        self.show_overlay(image, QApplication.primaryScreen().geometry(), {"backend": "XDG Screenshot Portal"}, kind)
                        return
                    self.finish_capture(image, {"backend": "XDG Screenshot Portal", "hdr_validated": False})
                except Exception as exc:
                    self.fail(str(exc))
            else:
                screen = QApplication.primaryScreen()
                from .editor import qimage_to_pil
                image = qimage_to_pil(screen.grabWindow(0).toImage())
                if kind in ("region", "timelapse", "measure"):
                    self.show_overlay(image, screen.geometry(), {"backend": "Qt X11", "hdr_validated": False}, kind)
                else:
                    self.finish_capture(image, {"backend": "Qt X11", "hdr_validated": False})

    def windows_result(self, monitors, kind):
        if self._capture_cancelled:
            return
        if kind == "all":
            left, top = min(m.bounds[0] for m in monitors), min(m.bounds[1] for m in monitors)
            right, bottom = max(m.bounds[2] for m in monitors), max(m.bounds[3] for m in monitors)
            if (right - left) * (bottom - top) > 150_000_000:
                self.fail("Der Gesamtdesktop überschreitet 150 Megapixel.")
                return
            image = Image.new("RGB", (right - left, bottom - top))
            for monitor in monitors:
                image.paste(monitor.image, (monitor.bounds[0] - left, monitor.bounds[1] - top))
            metadata = {"backend": "Windows Graphics Capture · alle Monitore", "monitors": [m.metadata for m in monitors], "desktop_bounds": [left, top, right, bottom]}
            self.remember_region((0, 0, right - left, bottom - top), metadata)
            self.finish_capture(image, metadata)
            return
        from PySide6.QtGui import QCursor
        from .capture_options import pick_screen
        cursor = QCursor.pos()
        wanted = pick_screen([(s.name(), (s.geometry().x(), s.geometry().y(), s.geometry().width(), s.geometry().height())) for s in QApplication.screens()],
                             (cursor.x(), cursor.y()), self.screen_combo.currentData(), self.settings.get("follow_mouse_screen", False))
        screen = next((s for s in QApplication.screens() if s.name() == wanted), QApplication.primaryScreen())
        geo = screen.geometry()
        # Recent Qt versions expose friendly display names instead of DISPLAYn.
        # On Windows Qt preserves native monitor origins while scaling sizes.
        monitor = next((m for m in monitors if m.name == wanted), None)
        if monitor is None:
            monitor = next((m for m in monitors if m.bounds[0] == geo.x() and m.bounds[1] == geo.y()), monitors[0])
        if kind == "screen":
            self.remember_region((0, 0, monitor.image.width, monitor.image.height), monitor.metadata)
            self.finish_capture(monitor.image, monitor.metadata)
            return
        self.show_overlay(monitor.image, screen.geometry(), monitor.metadata, kind)

    def show_overlay(self, image, geometry, metadata, kind="region"):
        if self._capture_cancelled:
            return
        self._stop_capture_escape_watch()
        metadata = dict(metadata, capture_kind=kind)
        cursor_pos = None
        saved_cursor = getattr(self, "_capture_cursor_pos", None)
        if saved_cursor is not None and geometry.contains(saved_cursor):
            rel = saved_cursor - geometry.topLeft()
            sx, sy = image.width / max(1, geometry.width()), image.height / max(1, geometry.height())
            cursor_pos = (max(0, min(image.width - 1, round(rel.x() * sx))),
                          max(0, min(image.height - 1, round(rel.y() * sy))))
        self._capture_cursor_pos = None
        overlay = SelectionOverlay(image, geometry, show_cursor=bool(self.settings.get("capture_cursor")), cursor_pos=cursor_pos)
        overlay.cursorToggled.connect(self.cursor_capture_toggled)
        overlay.pick_color = kind == "color"
        if kind == "measure":
            overlay.set_measure_mode(True)
        self.overlays.append(overlay)
        if kind in ("window", "region") and sys.platform == "win32":
            from .windows_capture import window_rectangles
            # Match native desktop bounds to the selected Qt logical monitor.
            from PySide6.QtCore import QRect
            sx, sy = image.width / geometry.width(), image.height / geometry.height()
            # Display bounds from DXGI are available in metadata after capture.
            bounds = metadata.get("desktop_bounds", [geometry.x(), geometry.y()])
            rectangles = []
            for title, (left, top, right, bottom), handle in window_rectangles(include_handles=True):
                local = QRect(round((left - bounds[0]) / sx), round((top - bounds[1]) / sy),
                              round((right - left) / sx), round((bottom - top) / sy)).intersected(overlay.rect())
                if local.width() > 10 and local.height() > 10:
                    rectangles.append((title, local))
                    overlay.window_handles.append(handle)
            overlay.windows = rectangles
        elif kind in ("window", "region") and sys.platform != "win32":
            from .integrations import hyprland_window_rectangles
            from PySide6.QtCore import QRect
            sx, sy = image.width / max(1, geometry.width()), image.height / max(1, geometry.height())
            bounds = metadata.get("desktop_bounds", [geometry.x(), geometry.y()])
            rectangles = []
            for title, (left, top, right, bottom) in hyprland_window_rectangles():
                local = QRect(round((left - bounds[0]) / sx), round((top - bounds[1]) / sy),
                              round((right - left) / sx), round((bottom - top) / sy)).intersected(overlay.rect())
                if local.width() > 10 and local.height() > 10:
                    rectangles.append((title, local))
            overlay.windows = rectangles
        overlay.regionSelected.connect(lambda box: self.remember_region(box, metadata))
        overlay.colorSelected.connect(lambda value: self.color_picked(overlay, value))
        overlay.windowSelected.connect(lambda handle: self.capture_selected_window(overlay, handle))
        overlay.selected.connect(lambda image: self.overlay_finished(overlay, image, metadata))
        overlay.cancelled.connect(lambda: self.overlay_finished(overlay, None, metadata))
        overlay.show()
        overlay.raise_()
        overlay.activateWindow()

    def cursor_capture_toggled(self, enabled):
        """M in the capture mode: remember whether the mouse cursor is part of the following captures."""
        self.settings["capture_cursor"] = bool(enabled)
        self.save_settings()
        self.statusBar().showMessage("Mauszeiger wird ab der nächsten Aufnahme " + ("mit aufgenommen" if enabled else "nicht aufgenommen"))

    def overlay_finished(self, overlay, image, metadata):
        if overlay in self.overlays:
            self.overlays.remove(overlay)
        if image is None and metadata.get("capture_kind") == "measure":
            self._stop_capture_escape_watch()
            self._capture_cancelled = False
            self.capture_pending = False
            self.capture_button.setEnabled(True)
            self.restore()
            self.statusBar().showMessage("Lineal geschlossen")
            return
        if image is not None and metadata.get("capture_kind") == "scroll":
            self.start_scroll_capture()
            return
        if image is not None and metadata.get("capture_kind") == "manual-scroll":
            self.start_manual_scroll(image)
            return
        if image is not None and metadata.get("capture_kind") == "video":
            self.capture_pending = False
            self.capture_button.setEnabled(True)
            self.adjust_video_region(self.settings.get("last_region"))
            return
        if image is not None and metadata.get("capture_kind") == "timelapse":
            self.capture_pending = False
            self.capture_button.setEnabled(True)
            self.begin_timelapse(self.settings.get("last_region"))
            return
        self.finish_capture(image, metadata)

    def adjust_video_region(self, region):
        """Let the user move/resize the chosen area (handles, standard sizes) before the recording starts."""
        if not region:
            self.begin_video_recording(region)
            return
        from .regionadjust import RegionAdjuster
        screen = self.screen_for_region(region)
        scale = screen.devicePixelRatio() if (sys.platform == "win32" and screen) else 1
        adjuster = RegionAdjuster(region, scale)
        self.region_adjuster = adjuster
        def started(chosen):
            self.settings["last_region"] = chosen
            self.save_settings()
            QTimer.singleShot(250, lambda: self.begin_video_recording(chosen))      # let the handles disappear first
        adjuster.started.connect(started)
        adjuster.cancelled.connect(lambda: (self.restore(), self.statusBar().showMessage("Videoaufnahme abgebrochen")))
        adjuster.show()
        adjuster.raise_()
        adjuster.activateWindow()

    def capture_selected_window(self, overlay, handle):
        if overlay in self.overlays:
            self.overlays.remove(overlay)
        from .windows_capture import capture_window
        def finished(result):
            self.remember_region((0, 0, result.image.width, result.image.height), result.metadata)
            self.finish_capture(result.image, result.metadata)
        QTimer.singleShot(150, lambda: self.task(
            lambda: capture_window(handle, self.settings["tone_mapping"], self.settings["capture_cursor"]),
            finished, "Fenster wird direkt aufgenommen …"))

    def color_picked(self, overlay, value):
        if overlay in self.overlays:
            self.overlays.remove(overlay)
        self.capture_pending = False
        self.capture_button.setEnabled(True)
        QApplication.clipboard().setText(value)
        self.restore()
        from .color_picker import color_formats
        formats = color_formats(value)
        dialog = QDialog(self)
        dialog.setWindowTitle("Farbpipette · Ausgabeformat")
        layout = QVBoxLayout(dialog)
        combo = QComboBox()
        combo.addItems(list(formats))
        output = QLineEdit(formats["HEX"])
        output.setReadOnly(True)
        def changed(name):
            output.setText(formats[name])
            QApplication.clipboard().setText(formats[name])
        combo.currentTextChanged.connect(changed)
        layout.addWidget(combo)
        layout.addWidget(output)
        swatch = QLabel(" ")
        swatch.setMinimumHeight(50)
        swatch.setStyleSheet(f"background: {value}; border-radius: 4px")
        layout.addWidget(swatch)
        layout.addWidget(QLabel("Der ausgewählte Farbwert liegt in der Zwischenablage."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def remember_region(self, box, metadata):
        origin = metadata.get("desktop_bounds", [0, 0])
        left, top, right, bottom = box
        self.settings["last_region"] = [origin[0] + left, origin[1] + top, right - left, bottom - top]
        self.settings["last_region_platform"] = sys.platform
        self.save_settings()

    def save_settings(self):
        apply_policy_locked = getattr(self, "policy", None)
        if apply_policy_locked:
            self.settings.update(apply_policy_locked["locked"])
        temporary = self.settings_path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.settings_path)
            return True
        except OSError as exc:
            import logging
            logging.warning("Einstellungen konnten nicht gespeichert werden: %s", exc)
            self.statusBar().showMessage("Einstellungen nicht gespeichert: Datenordner ist nicht beschreibbar")
            return False

    def omarchy_result(self, result, kind):
        if self._capture_cancelled or result is None:
            self.finish_capture(None, {})
            return
        image, bounds = result
        if bounds:
            self.settings.update(last_region=list(bounds), last_region_platform=sys.platform)
            self.save_settings()
        if kind == "scroll":
            self.start_scroll_capture()
        elif kind == "manual-scroll":
            self.start_manual_scroll(image)
        elif kind == "video":
            self.capture_pending = False
            self.capture_button.setEnabled(True)
            self.begin_video_recording(bounds)
        elif kind == "timelapse":
            self.capture_pending = False
            self.capture_button.setEnabled(True)
            self.begin_timelapse(bounds)
        elif kind == "measure":
            screen = self.screen_for_region()
            self.show_overlay(image, screen.geometry(), {"backend": "Hyprland / grim"}, kind)
        else:
            self.finish_capture(image, {"backend": "Hyprland / grim", "hdr_validated": False})

    def start_scroll_capture(self):
        bounds = self.settings.get("last_region")
        if not bounds:
            self.fail("Kein Scrollbereich ausgewählt.")
            return
        self.scroll_cancel.clear()
        options = self.scroll_options()
        if options is None:
            self.finish_capture(None, {})
            return
        self.hide()
        self._start_capture_escape_watch()
        from .capture import scroll_capture
        QTimer.singleShot(350, lambda: self.task(
            lambda: scroll_capture(bounds, self.settings["tone_mapping"], cancel=self.scroll_cancel, **options),
            self.scroll_capture_finished, "Scrollaufnahme läuft · Ende über das Tray-Menü oder Esc"))

    def scroll_options(self):
        from PySide6.QtWidgets import QFormLayout
        dialog = QDialog(self)
        dialog.setWindowTitle("Scrollaufnahme")
        form = QFormLayout(dialog)
        direction = QComboBox()
        direction.addItem("Nach unten", "vertical")
        direction.addItem("Nach rechts", "horizontal")
        form.addRow("Scrollrichtung", direction)
        header, footer = QSpinBox(), QSpinBox()
        for box in (header, footer):
            box.setRange(0, 2000)
            box.setSuffix(" px")
        form.addRow("Feste Kopf-/linke Leiste", header)
        form.addRow("Feste Fuß-/rechte Leiste", footer)
        hint = QLabel("Feste Leisten werden einmal übernommen.\nBei manueller Serie: selbst scrollen, Strg+Alt+F7 für das nächste Bild, Strg+Alt+F8 zum Fertigstellen.")
        hint.setWordWrap(True)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return {"direction": direction.currentData(), "header": header.value(), "footer": footer.value()}

    def start_manual_scroll(self, image):
        if self.manual_scroll:
            self.fail("Bitte zuerst die laufende Scrollserie fertigstellen.")
            return
        options = self.scroll_options()
        if options is None:
            self.finish_capture(None, {})
            return
        from .scroll_session import ManualScrollSession
        self.manual_scroll = ManualScrollSession(self.workspace.root / "scroll-parts", self.settings["last_region"], **options)
        self.manual_scroll.append(image)
        self.capture_pending = False
        self.capture_button.setEnabled(True)
        self.hide()
        self.tray.setToolTip("screenkaffee · Scrollserie: 1 Bild · Strg+Alt+F7: nächstes · F8: fertig")

    def manual_scroll_frame(self):
        if self.manual_scroll is None or self.capture_pending:
            return
        self.capture_pending = True
        self.hide()
        from .capture import capture_region
        session = self.manual_scroll
        def done(result):
            count = session.append(result[0])
            self.capture_pending = False
            self.tray.setToolTip(f"screenkaffee · Scrollserie: {count} Bilder")
        QTimer.singleShot(350, lambda: self.task(lambda: capture_region(session.bounds, self.settings["tone_mapping"]), done, "Scrollbild aufnehmen …"))

    def finish_manual_scroll(self):
        if self.manual_scroll is None or self.capture_pending:
            return
        session = self.manual_scroll
        self.capture_pending = True
        def done(image):
            self.manual_scroll = None
            self.tray.setToolTip("screenkaffee")
            self.finish_capture(image, {"backend": "Manuelle Live-Scrollserie", "frames": len(session.paths)})
        self.task(session.finish, done, "Scrollserie zusammensetzen …")

    def scroll_capture_finished(self, result):
        self.finish_capture(result.image, {"backend": "Automatische Scrollaufnahme", "frames": result.frames, "stop_reason": result.stop_reason})

    def finish_capture(self, image, metadata):
        self._stop_capture_escape_watch()
        if self._capture_cancelled:
            image = None
        self._capture_cancelled = False
        self.capture_pending = False
        self.capture_button.setEnabled(True)
        if image is None:
            self.restore()
            self.statusBar().showMessage("Aufnahme abgebrochen")
            return
        try:
            metadata = dict(metadata or {})
            if self.settings.get("capture_context", True):
                from .capture_options import collect_capture_context
                context_info = collect_capture_context()
                for key, value in context_info.items():
                    if value and not metadata.get(key):
                        metadata[key] = value
            image = post_process(image, self.settings)
            if self.settings.get("clipboard_only"):
                QApplication.clipboard().setImage(pil_to_qimage(image))
                self.restore()
                self.statusBar().showMessage(f"{image.width} × {image.height} px in die Zwischenablage kopiert (nicht gespeichert)")
                self.print_if_requested(image)
                return
            number = len(self.workspace.rows()) + 1
            ident = self.workspace.add(image, title=title_for(self.settings.get("title_pattern"), number, context=metadata), metadata=metadata)
            self.apply_retention(exclude={ident})
            self.refresh()
            self.select_id(ident)
            if self.settings["auto_copy"]:
                QApplication.clipboard().setImage(pil_to_qimage(image))
            self.restore()
            self.print_if_requested(image)
            if self.settings["auto_edit"]:
                self.open_editor(ident)
            self.statusBar().showMessage(f"{image.width} × {image.height} px gespeichert · {metadata.get('backend', 'Import')}")
        except Exception as exc:
            self.fail(str(exc))

    def print_if_requested(self, image):
        if self.settings.get("auto_print") and not self.blocked("print"):
            from .printing import print_images
            print_images(self, [image], self.settings.get("print_margin_mm", 0))

    def refresh_collections(self):
        was_trash = self.in_trash()
        selected = self.collection.currentText()
        self.collection.blockSignals(True)
        self.collection.clear()
        self.collection.addItem("Alle Aufnahmen")
        for value in self.workspace.collections():
            if value != "Alle Aufnahmen":
                self.collection.addItem(value)
        trash_label = f"Papierkorb ({len(self.workspace.trash_entries())})"
        from .icons import round_icon
        self.collection.addItem(round_icon("trash", "#dca354", 30), trash_label, "trash")
        self.collection.setCurrentText(trash_label if was_trash else selected)
        self.collection.blockSignals(False)
        from .icons import round_icon
        self.collection_list.blockSignals(True)
        self.collection_list.clear()
        for index in range(self.collection.count()):
            name = self.collection.itemText(index)
            self.collection_list.addItem(name)
            if self.collection.itemData(index) == "trash":
                self.collection_list.item(index).setIcon(round_icon("trash", "#dca354", 30))
                continue
            glyph = self.workspace.collection_icon(name) if index else ""
            self.collection_list.item(index).setIcon(round_icon(glyph if glyph in COLLECTION_ICONS else ("document" if index else "camera"), "#b0b0b0", 30))
        self.collection_list.addItem("+ Neu …")
        self.collection_list.item(self.collection_list.count() - 1).setIcon(round_icon("more", "#b0b0b0", 30))
        self.collection_list.blockSignals(False)
        self.sync_collection_list()

    def refresh(self, *args):
        if not hasattr(self, "thumbnail_loader"):
            self.thumbnail_loader = ThumbnailLoader(self)
        self.thumbnail_loader.reset()
        selected = set(self.selected_ids(include_trash=True))
        self.list.clear()
        trash_view = self.in_trash()
        for button in self.media_buttons:
            button.setEnabled(not trash_view)
        for action in self.more_menu.actions():
            if action.text() in ("Auswahl bearbeiten", "Teilen und ausgeben"):
                action.setEnabled(not trash_view)
        for button in self.trash_buttons:
            button.setVisible(trash_view)
        rows = self.workspace.trash_entries() if trash_view else self.workspace.rows(self.search.text(), self.collection.currentText(), kind=self.type_filter.currentData(),
            sort=self.sort_order.currentData(), days=self.time_filter.currentData())
        for row in rows:
            fav = bool(row["favorite"])
            item = QListWidgetItem(("★ " + row["title"]) if fav else row["title"])
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setToolTip(f"{row['title']}\n{row['width']} × {row['height']} px\n{row['tags']}".strip())
            if trash_view:
                thumb = self.workspace.root / "trash" / (row["id"] + ".thumb.png")
                if not thumb.exists():
                    thumb = self.workspace.root / "trash" / row["filename"]
            else:
                thumb = self.workspace.thumbnail_path(row["id"])
            self.thumbnail_loader.enqueue(item, thumb, row["kind"], favorite=fav)
            self.list.addItem(item)
            item.setSelected(row["id"] in selected)
        self.count_label.setText(f"{len(rows)} Aufnahmen")

    def in_trash(self):
        return self.collection.currentData() == "trash"

    def selected_ids(self, include_trash=False):
        if self.in_trash() and not include_trash:
            return []
        return [x.data(Qt.ItemDataRole.UserRole) for x in self.list.selectedItems()]

    def select_id(self, ident):
        self.list.clearSelection()
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == ident:
                item.setSelected(True)
                self.list.setCurrentItem(item)
                self.list.scrollToItem(item)
                break

    def require_one(self):
        ids = self.selected_ids()
        if not ids:
            self.statusBar().showMessage("Bitte zuerst eine Aufnahme auswählen.")
            return None
        return ids[0]

    def show_preview(self):
        ids = self.selected_ids()
        if not ids:
            return
        row = self.workspace.get(ids[0])
        pix = QPixmap(str(self.workspace.thumbnail_path(ids[0])))
        dpr = self.devicePixelRatioF() or 1.0
        scaled = pix.scaled(round(300 * dpr), round(240 * dpr), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(dpr)
        self.preview.setPixmap(scaled)
        meta = json.loads(row["metadata"])
        color = f"HDR → sRGB · SDR-Weiß {meta.get('sdr_white_nits')} cd/m²" if meta.get("hdr") else "sRGB-PNG"
        self.detail.setText(f"{row['title']}\n\n{row['width']} × {row['height']} Pixel\n{row['created']}\n{color}\n{meta.get('backend', 'Import')}\n\n{row['tags']}")

    def open_editor(self, ident):
        try:
            row = self.workspace.get(ident)
            if row["kind"] == "video":
                from .media_editor import VideoEditor
                editor = VideoEditor(self.workspace.path(ident), animation_fps=self.settings.get("animation_fps", 15),
                                     animation_loops=self.settings.get("animation_loops", 0))
                editor.capture_ident = ident
                editor.prepare_speech = self.prepare_speech
                editor.videoSaved.connect(self.import_video_path)
                editor.frameSaved.connect(lambda image: self.add_edited(image, row["title"] + " · Videobild"))
                self.editors.append(editor)
                editor.destroyed.connect(lambda: self.editors.remove(editor) if editor in self.editors else None)
                editor.show()
                return
            editor = EditorWindow(self.workspace.image(ident), row["title"], self.workspace.project_path(ident))
            editor.capture_ident = ident
            editor.beautify_settings = self.settings          # zuletzt gewählter Stil gilt auch im Editor
            editor.saved.connect(lambda image: self.add_edited(image, row["title"]))
            editor.projectSaved.connect(lambda image, ident=ident: (
                self.workspace.update_rendered_thumbnail(ident, image), self.refresh(), self.show_preview()))
            editor.navigate.connect(lambda step, ident=ident, editor=editor: self.navigate_editor(editor, ident, step))
            self.editors.append(editor)
            editor.destroyed.connect(lambda: self.editors.remove(editor) if editor in self.editors else None)
            editor.show()
        except Exception as exc:
            self.fail(str(exc))

    def open_external_image(self, path):
        """Open an image file in its own editor (command line / "open with"); it is not imported."""
        from .color import import_srgb
        try:
            if not Path(path).is_file():
                raise ValueError("Datei nicht gefunden: " + str(path))
            editor = EditorWindow(import_srgb(path), Path(path).name)
        except Exception as exc:
            self.fail(str(exc))
            return None
        self.editors.append(editor)
        editor.destroyed.connect(lambda: self.editors.remove(editor) if editor in self.editors else None)
        editor.show()
        return editor

    def neighbor_image_id(self, ident, step):
        """Id of the previous/next image in the workspace list (videos are skipped), or None."""
        ids = [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]
        if ident not in ids:
            return None
        index = ids.index(ident) + step
        while 0 <= index < len(ids):
            if self.workspace.get(ids[index])["kind"] == "image":
                return ids[index]
            index += step
        return None

    def navigate_editor(self, editor, ident, step, delete_current=False):
        """PageUp/PageDown in the editor: continue with the neighbouring image of the workspace (Alt+Shift deletes current)."""
        mods = QApplication.keyboardModifiers()
        alt_shift = (mods & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier)) == (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier)
        should_delete = delete_current or alt_shift
        neighbor = self.neighbor_image_id(ident, step)
        if neighbor is None and not should_delete:
            editor.statusBar().showMessage("Kein weiteres Bild in dieser Richtung.")
            return
        if should_delete:
            editor.view.dirty = False
        if editor.close():
            if should_delete and self.workspace.get(ident) is not None:
                self.workspace.remove(ident)
                self.refresh()
            if neighbor is not None and self.workspace.get(neighbor) is not None:
                self.select_id(neighbor)
                self.open_editor(neighbor)

    def add_edited(self, image, title):
        ident = self.workspace.add(image, title + " · bearbeitet", {"backend": "Editor"})
        self.apply_retention(exclude={ident})
        self.refresh()
        self.select_id(ident)

    def edit_selected(self):
        ident = self.require_one()
        if ident:
            self.open_editor(ident)

    def pin_selected(self):
        ident = self.require_one()
        if not ident:
            return None
        try:
            image = self.rendered(ident)
        except Exception as exc:
            self.fail(str(exc))
            return None
        from .pin import pin
        return pin(image, self.workspace.get(ident)["title"] or "", on_edit=lambda _image, i=ident: self.open_editor(i))

    def beautify_selected(self):
        """Farbverlauf, Schatten, Ecken und Fensterrahmen; Ergebnis als neue Aufnahme oder in die Zwischenablage."""
        ident = self.require_one()
        if not ident:
            return None
        try:
            image = self.rendered(ident)
        except Exception as exc:
            self.fail(str(exc))
            return None
        title = self.workspace.get(ident)["title"] or ""
        from .beautify import beautify_dialog
        def save(result):
            new = self.workspace.add(result, title + " · schick", {"backend": "Schicker Screenshot"})
            self.refresh()
            self.select_id(new)
        dialog = beautify_dialog(self, image, self.settings, on_save=save, title=title)
        dialog.finished.connect(lambda _: self.save_settings())
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        return dialog

    def compare_selected(self):
        """Zwei Bildaufnahmen nebeneinander: die ältere links (vorher), die neuere rechts (nachher)."""
        ids = self.selected_ids()
        if len(ids) != 2:
            QMessageBox.information(self, "Vergleichen", "Bitte genau zwei Bildaufnahmen auswählen (Strg+Klick).")
            return None
        rows = sorted((self.workspace.get(ident) for ident in ids), key=lambda row: row["created"])
        try:
            images = [self.rendered(row["id"]) for row in rows]
        except Exception as exc:
            self.fail(str(exc))
            return None
        titles = [row["title"] or "Aufnahme" for row in rows]
        from .compare import compare
        def save(image):
            ident = self.workspace.add(image, f"Vergleich {titles[0]} ↔ {titles[1]}", {"backend": "Vergleich"})
            self.refresh()
            self.select_id(ident)
        return compare(*images, titles=titles, on_save=save)

    def share_to_phone(self):
        """QR-Code für die ausgewählten Aufnahmen; ein kleiner Webserver im WLAN liefert sie nur bis zum Schließen aus."""
        if self.blocked("lan_share"):
            return None
        ids = self.selected_ids()
        if not ids:
            self.statusBar().showMessage("Bitte zuerst eine Aufnahme auswählen.")
            return None
        import shutil
        import uuid
        from .batch import safe_name
        staging = self.workspace.root / "phone-share" / uuid.uuid4().hex
        files = []
        try:
            for ident in ids:
                row = self.workspace.get(ident)
                title = row["title"] or "Aufnahme"
                if row["kind"] == "image":
                    staging.mkdir(parents=True, exist_ok=True)
                    path = staging / f"{len(files) + 1}-{safe_name(title)}.png"
                    save_png(self.rendered(ident), path)
                else:
                    path = self.workspace.path(ident)
                files.append((path, title))
            from .lanshare import share_dialog
            dialog = share_dialog(self, files, cleanup=lambda: shutil.rmtree(staging, ignore_errors=True))
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            self.fail(str(exc))
            return None
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()
        return dialog

    def rendered(self, ident):
        if self.workspace.get(ident)["kind"] != "image":
            raise ValueError("Für diese Funktion bitte eine Bildaufnahme auswählen.")
        project = self.workspace.project_path(ident)
        image = self.workspace.image(ident)
        if project.exists():
            from .editor import EditorView
            view = EditorView(image)
            try:
                view.load_project(json.loads(project.read_text(encoding="utf-8")))
                return view.render_image()
            finally:
                view.deleteLater()
        return image

    def copy_selected(self):
        ident = self.require_one()
        if ident:
            try:
                if self.workspace.get(ident)["kind"] != "image":
                    from PySide6.QtCore import QMimeData
                    mime = QMimeData()
                    mime.setUrls([QUrl.fromLocalFile(str(self.workspace.path(ident).resolve()))])
                    QApplication.clipboard().setMimeData(mime)
                    return
                QApplication.clipboard().setImage(pil_to_qimage(self.rendered(ident)))
                self.statusBar().showMessage("Bild einschließlich gespeicherter Markierungen kopiert")
            except Exception as exc:
                self.fail(str(exc))

    def export_selected(self):
        ids = self.selected_ids()
        if not ids:
            return
        if len(ids) == 1:
            default_suffix = self.settings.get("export_format", ".png")
            filters = {".png": "PNG (*.png)", ".jpg": "JPEG (*.jpg)", ".jxl": "JPEG XL (*.jxl)", ".webp": "WebP (*.webp)", ".tiff": "TIFF (*.tiff)",
                       ".bmp": "BMP (*.bmp)", ".gif": "GIF (*.gif)", ".pdf": "PDF (*.pdf)", ".docx": "Word (*.docx)"}
            ordered = [filters[default_suffix]] + [text for key, text in filters.items() if key != default_suffix] if default_suffix in filters else list(filters.values())
            from .batch import safe_name
            suggested = safe_name(self.workspace.get(ids[0])["title"])
            path, selected = QFileDialog.getSaveFileName(self, "Bild exportieren", suggested + default_suffix, ";;".join(ordered))
            if path and not Path(path).suffix:
                import re
                path += re.search(r"\*([.a-z]+)", selected).group(1)
            if not path:
                return
            try:
                from .batch import export_any
                export_any(self.rendered(ids[0]), self.workspace.get(ids[0])["title"], path)
                self.statusBar().showMessage(f"Exportiert: {path}")
            except Exception as exc:
                self.fail(str(exc))
            return
        folder = QFileDialog.getExistingDirectory(self, "Exportordner wählen")
        if not folder:
            return
        try:
            from .batch import export_batch
            items = [(self.workspace.get(ident)["title"], self.rendered(ident)) for ident in ids]
            suffix = self.settings.get("export_format", ".png")
            written = export_batch(items, folder, suffix if suffix in (".png", ".jpg", ".jxl", ".webp", ".tiff", ".bmp", ".gif") else ".png")
            self.statusBar().showMessage(f"{len(written)} Datei(en) exportiert nach {folder}")
        except Exception as exc:
            self.fail(str(exc))

    def import_paths(self, paths):
        import base64
        import io
        failures, last = [], None
        for path in paths:
            try:
                p = Path(path)
                if p.name.lower().endswith((".screenkaffee.json", ".prism.json")):
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if "image_png_base64" in data:
                        img = Image.open(io.BytesIO(base64.b64decode(data["image_png_base64"]))).convert("RGB")
                    else:
                        companion = p.with_name(p.name.rsplit(".", 2)[0] + ".png")
                        img = import_srgb(companion)
                    title = data.get("title") or p.name.rsplit(".", 2)[0]
                    last = self.workspace.add(img, title, {"backend": "Projektimport"})
                    if data.get("tags"):
                        row = self.workspace.get(last)
                        self.workspace.update(last, row["title"], data["tags"], row["collection"])
                    project_payload = {k: v for k, v in data.items() if k in ("version", "crop", "effects", "annotations", "replacement_image")}
                    self.workspace.project_path(last).write_text(json.dumps(project_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    continue
                if p.suffix.lower() in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
                    self.import_video_path(str(path))
                    continue
                last = self.workspace.add(import_srgb(path), p.stem, {"backend": "Dateiimport"})
            except Exception as exc:
                failures.append(f"{Path(path).name}: {exc}")
        self.refresh()
        if last:
            self.select_id(last)
        if failures:
            self.fail("\n".join(failures))

    def import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Bilder importieren", "", "Bilder & Projekte (*.png *.jpg *.jpeg *.jxl *.webp *.bmp *.tif *.tiff *.screenkaffee.json)")
        self.import_paths(paths)

    def import_videos(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Videos importieren", "", "Videos (*.mp4 *.mkv *.avi *.mov *.webm *.gif)")
        for path in paths:
            self.import_video_path(path)

    @Slot(str)
    def import_video_path(self, path):
        from .media import probe_media, extract_frame
        def read():
            info = probe_media(path)
            return (str(path), vars(info), extract_frame(path))
        self.task(read, self.video_imported, "Video wird eingelesen …")

    def video_imported(self, result):
        path, metadata, frame = result
        ident = self.workspace.add_file(path, "video", metadata=dict(metadata, backend="Video"), thumbnail=frame)
        self.apply_retention(exclude={ident})
        self.refresh()
        self.select_id(ident)

    def merge_selected_videos(self):
        ids = self.selected_ids()
        if len(ids) < 2 or any(self.workspace.get(ident)["kind"] != "video" for ident in ids):
            QMessageBox.information(self, "Videos zusammenfügen", "Bitte mindestens zwei Videos auswählen. Reihenfolge: ältestes zuerst.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Zusammengeführtes Video speichern", "Kombiniert.mp4", "MP4 (*.mp4)")
        if path:
            from .media import merge_videos
            sources = [self.workspace.path(ident) for ident in reversed(ids)]
            self.task(lambda: merge_videos(sources, path), lambda result: self.import_video_path(str(result)), "Videos werden zusammengefügt …")

    def paste_image(self):
        image = QApplication.clipboard().image()
        if image.isNull():
            self.statusBar().showMessage("Kein Bild in der Zwischenablage")
            return
        from .editor import qimage_to_pil
        ident = self.workspace.add(qimage_to_pil(image), metadata={"backend": "Zwischenablage"})
        self.refresh()
        self.select_id(ident)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.import_paths([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()

    def stitch_selected(self):
        ids = self.selected_ids()
        if len(ids) < 2:
            QMessageBox.information(self, "Scrollbilder verbinden", "Mindestens zwei überlappende Bilder auswählen.\nDie Bilder werden in Aufnahmereihenfolge verbunden (ältestes zuerst).\nVorher ggf. feste Kopfzeilen im Editor zuschneiden.")
            return
        images = [self.rendered(ident) for ident in reversed(ids)]
        from .stitch import stitch_vertical
        self.task(lambda: stitch_vertical(images), lambda image: self.add_edited(image, "Scrollaufnahme"), "Scrollbilder werden ausgerichtet …")

    def show_text(self, title, text):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(760, 560)
        layout = QVBoxLayout(dialog)
        editor = QTextEdit()
        editor.setPlainText(text)
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy = buttons.addButton("Alles kopieren", QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(editor.toPlainText()))
        save = buttons.addButton("Text speichern …", QDialogButtonBox.ButtonRole.ActionRole)
        def save_text():
            path, _ = QFileDialog.getSaveFileName(dialog, "Text speichern", "Text.txt", "Text (*.txt)")
            if path:
                Path(path).write_text(editor.toPlainText(), encoding="utf-8")
        save.clicked.connect(save_text)
        if title == "Erkannter Text":
            translate = buttons.addButton("In Google Translate öffnen", QDialogButtonBox.ButtonRole.ActionRole)
            def open_translation():
                from PySide6.QtCore import QUrlQuery
                url = QUrl("https://translate.google.com/")
                query = QUrlQuery()
                for key, value in [("sl", "auto"), ("tl", "de"), ("text", editor.toPlainText()), ("op", "translate")]:
                    query.addQueryItem(key, value)
                url.setQuery(query)
                QDesktopServices.openUrl(url)
            translate.clicked.connect(open_translation)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def qr_selected(self):
        ident = self.require_one()
        if not ident:
            return
        try:
            import zxingcpp
            results = zxingcpp.read_barcodes(np.asarray(self.rendered(ident)))
            self.show_text("QR- und Barcodes", "\n\n".join(x.text for x in results) or "Kein Code gefunden.")
        except ImportError:
            self.fail("QR-Erkennung benötigt das optionale Paket zxing-cpp.")
        except Exception as exc:
            self.fail(str(exc))

    def webpage(self):
        if self.blocked("webpage"):
            return
        from PySide6.QtWidgets import QInputDialog
        from .webpage import capture_webpage
        url, ok = QInputDialog.getText(self, "Webseite aufnehmen", "Adresse (https://…):")
        if not ok or not url.strip():
            return
        url = url.strip()
        if "://" not in url:
            url = "https://" + url
        self.task(lambda: capture_webpage(url), lambda image: self.finish_capture(image, {"backend": "Browser", "url": url}),
                  "Webseite wird aufgenommen …")

    def android_video(self):
        if self.blocked("android"):
            return
        recorder = getattr(self, "android_recorder", None) or AndroidRecorder()
        self.android_recorder = recorder
        if recorder.active:
            self.android_video_action.setText("Android-Video starten")
            self.task(lambda: recorder.stop(self.workspace.root / "videos"),
                      lambda path: path and self.import_video_path(str(path)), "Android-Video wird geladen …")
            return
        try:
            recorder.start()
        except Exception as exc:
            return self.fail(str(exc))
        self.android_video_action.setText("Android-Video stoppen und speichern")
        self.statusBar().showMessage("Android-Aufnahme läuft (Gerätelimit 3 Minuten)")

    def android(self):
        if self.blocked("android"):
            return
        self.task(android_capture, lambda image: self.finish_capture(image, {"backend": "Android adb"}), "Android-Screenshot …")

    def document_export(self):
        ids = list(reversed(self.selected_ids()))
        if not ids:
            return
        try:
            entries = [(self.workspace.get(ident)["title"], self.rendered(ident)) for ident in ids]
            from .document_designer import DocumentDesigner
            DocumentDesigner(entries, self.workspace.root, self).exec()
        except Exception as exc:
            self.fail(str(exc))

    def export_copy(self, ident, title, extension):
        """Save a converted copy of one image next to a location the user picks."""
        from .batch import safe_name
        from .color import save_image
        path, _ = QFileDialog.getSaveFileName(self, "Kopie speichern", safe_name(title) + extension, f"Bild (*{extension})")
        if path:
            try:
                save_image(self.rendered(ident), path, metadata={"Software": "screenkaffee"})
            except Exception as exc:
                self.fail(str(exc))

    def batch_export_selected(self):
        ids = [i for i in self.selected_ids() if self.workspace.get(i)["kind"] == "image"]
        if not ids:
            self.statusBar().showMessage("Bitte mindestens eine Bildaufnahme auswählen.")
            return
        from PySide6.QtWidgets import QFormLayout, QDoubleSpinBox
        from .batch import EXPORT_FORMATS, export_batch
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Stapelexport: {len(ids)} Bild(er)")
        form = QFormLayout(dialog)
        fmt = QComboBox()
        for suffix in EXPORT_FORMATS:
            fmt.addItem(suffix[1:].upper(), suffix)
        form.addRow("Format", fmt)
        scale = QSpinBox()
        scale.setRange(5, 400)
        scale.setValue(100)
        scale.setSuffix(" %")
        form.addRow("Größe", scale)
        quality = QSpinBox()
        quality.setRange(1, 100)
        quality.setValue(90)
        form.addRow("Qualität (JPEG/WebP)", quality)
        border = QSpinBox()
        border.setRange(0, 100)
        form.addRow("Rahmenbreite", border)
        radius = QSpinBox()
        radius.setRange(0, 500)
        form.addRow("Eckenradius", radius)
        shadow = QSpinBox()
        shadow.setRange(0, 200)
        form.addRow("Schattenweichheit", shadow)
        gray = QCheckBox("Graustufen")
        form.addRow(gray)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        folder = QFileDialog.getExistingDirectory(self, "Exportordner wählen")
        if not folder:
            return
        effects = {"scale_percent": scale.value(), "border_width": border.value(), "corner_radius": radius.value(),
                   "shadow_radius": shadow.value(), "grayscale": gray.isChecked()}
        suffix, q = fmt.currentData(), quality.value()
        items = [(self.workspace.get(i)["title"], self.rendered(i)) for i in ids]
        self.task(lambda: export_batch(items, folder, suffix, effects, q),
                  lambda paths: self.statusBar().showMessage(f"{len(paths)} Bild(er) exportiert nach {folder}"),
                  "Stapelexport läuft …")

    def recompress_selected(self):
        ids = [i for i in self.selected_ids() if self.workspace.get(i)["kind"] == "image"]
        if not ids:
            self.statusBar().showMessage("Bitte mindestens eine Bildaufnahme auswählen.")
            return
        from .batch import recompress_png
        paths = [self.workspace.path(i) for i in ids]
        self.task(lambda: sum(recompress_png(p) for p in paths),
                  lambda saved: self.statusBar().showMessage(f"Neu komprimiert: {saved / 1024:.1f} KB eingespart (verlustfrei)"),
                  "Bilder werden neu komprimiert …")

    def renumber_selected(self):
        ids = self.selected_ids()
        if len(ids) < 1:
            self.statusBar().showMessage("Bitte mindestens eine Aufnahme auswählen.")
            return
        prefix, ok = QInputDialog.getText(self, "Auto-Umbenennen", "Präfix für die Titel", text="screenshot")
        if not ok:
            return
        from .batch import renumber_titles
        for ident, title in renumber_titles([self.workspace.get(i) for i in ids], prefix):
            row = self.workspace.get(ident)
            self.workspace.update(ident, title, row["tags"], row["collection"])
        self.refresh()

    def quick_view_selected(self):
        ident = self.require_one()
        if not ident or self.workspace.get(ident)["kind"] != "image":
            return
        from .quickview import QuickView
        self.quick_view = QuickView(QPixmap.fromImage(pil_to_qimage(self.rendered(ident))))
        self.quick_view.show()
        self.quick_view.activateWindow()

    def toggle_favorite_selected(self):
        ids = self.selected_ids()
        if not ids:
            return
        all_fav = all(self.workspace.get(i)["favorite"] for i in ids)
        self.workspace.set_favorite(ids, not all_fav)
        self.refresh()
        self.show_preview()

    def edit_details(self):
        ids = self.selected_ids()
        if not ids:
            self.statusBar().showMessage("Bitte zuerst eine Aufnahme auswählen.")
            return None
        from .workspace import details_dialog
        def ask_copy(ident, title, extension):
            if QMessageBox.question(self, "Endung erkannt", f"Zusätzlich eine Kopie als {extension[1:].upper()} speichern?") == QMessageBox.StandardButton.Yes:
                self.export_copy(ident, title, extension)
        dialog = details_dialog(self, self.workspace, ids, on_export_copy=ask_copy)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            dialog.apply()
            self.refresh_collections()
            self.refresh()
            self.show_preview()
        return dialog

    def show_media_properties(self):
        ident = self.require_one()
        if ident:
            from .media_properties import media_properties
            row = dict(self.workspace.get(ident))
            path = self.workspace.path(ident)
            self.task(lambda: media_properties(path, row["kind"], json.loads(row["metadata"])),
                      lambda values: self.show_text("Medieneigenschaften", json.dumps(values, ensure_ascii=False, indent=2)),
                      "Medieneigenschaften lesen …")

    def new_collection(self):
        name, ok = QInputDialog.getText(self, "Neue Sammlung", "Name")
        if ok:
            try:
                self.workspace.create_collection(name)
                self.refresh_collections()
                self.collection.setCurrentText(name)
            except Exception as exc:
                self.fail(str(exc))

    def switch_workspace(self):
        if self.tasks or self.editors or self.recorder.active or self.capture_pending:
            QMessageBox.information(self, "Arbeitsbereich wechseln", "Bitte zuerst offene Editoren schließen und laufende Vorgänge beenden.")
            return
        known = self.settings.get("workspaces", [])
        labels = [str(path) for path in known] + ["Ordner auswählen …"]
        selected, ok = QInputDialog.getItem(self, "Arbeitsbereich wechseln", "Arbeitsbereich", labels, editable=False)
        if not ok:
            return
        path = QFileDialog.getExistingDirectory(self, "Arbeitsbereich-Ordner") if selected == labels[-1] else selected
        if not path:
            return
        try:
            new = Workspace(path)
            self.workspace.close()
            self.workspace = new
            self.settings["workspaces"] = list(dict.fromkeys([*known, str(Path(path).resolve())]))
            self.save_settings()
            self.search.clear()
            self.refresh_collections()
            self.refresh()
            self.statusBar().showMessage("Arbeitsbereich: " + str(self.workspace.root))
        except Exception as exc:
            self.fail(str(exc))

    def sync_local_folder(self):
        if self.tasks or self.editors or self.recorder.active or self.capture_pending:
            QMessageBox.information(self, "Arbeitsbereich abgleichen", "Bitte zuerst Editoren schließen und laufende Vorgänge beenden.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Lokalen Austauschordner auswählen")
        if not folder:
            return
        root = self.workspace.root
        propagate = bool(self.settings.get("sync_propagate_deletions", False))
        def operation():
            from .workspace_sync import sync_workspace
            workspace = Workspace(root)
            try:
                return sync_workspace(workspace, folder, propagate_deletions=propagate)
            finally:
                workspace.close()
        def finished(result):
            self.refresh_collections()
            self.refresh()
            deleted_info = f", {result['deleted']} gelöscht" if result.get("deleted") else ""
            self.statusBar().showMessage(f"Abgleich: {result['imported']} übernommen, {result['exported']} bereitgestellt, {result['conflicts']} Konfliktkopien{deleted_info}")
        self.task(operation, finished, "Arbeitsbereich wird mit lokalem Ordner abgeglichen …")

    def print_selected(self):
        if self.blocked("print"):
            return
        ids = self.selected_ids()
        if not ids:
            return
        try:
            from .printing import print_images
            print_images(self, [self.rendered(ident) for ident in reversed(ids)], self.settings.get("print_margin_mm", 0))
        except Exception as exc:
            self.fail(str(exc))

    def mail_draft(self):
        ids = self.selected_ids()
        if not ids:
            return
        destination, _ = QFileDialog.getSaveFileName(self, "E-Mail-Entwurf speichern", "screenkaffee.eml", "E-Mail-Entwurf (*.eml)")
        if not destination:
            return
        try:
            import io
            from .local_share import save_mail_draft
            attachments = []
            for ident in ids:
                row = self.workspace.get(ident)
                if row["kind"] == "image":
                    buffer = io.BytesIO()
                    self.rendered(ident).save(buffer, format="PNG")
                    attachments.append((f"screenkaffee-{ident}.png", buffer.getvalue()))
                else:
                    path = self.workspace.path(ident)
                    attachments.append((path.name, path.read_bytes()))
            save_mail_draft(destination, attachments)
            self.statusBar().showMessage("E-Mail-Entwurf lokal gespeichert; zum Versenden im Mailprogramm öffnen")
        except Exception as exc:
            self.fail(str(exc))

    def apply_retention(self, exclude=(), confirm=False):
        protected = set(exclude) | {getattr(editor, "capture_ident", None) for editor in self.editors}
        candidates = self.workspace.cleanup_candidates(self.settings.get("retention_count", 0),
            self.settings.get("retention_days", 0), protected)
        if confirm:
            if not candidates:
                QMessageBox.information(self, "Aufbewahrung", "Keine Aufnahmen entsprechen den aktuellen Aufräumregeln.")
                return
            if QMessageBox.question(self, "Aufräumen", f"{len(candidates)} Aufnahmen in den wiederherstellbaren Papierkorb verschieben?") != QMessageBox.StandardButton.Yes:
                return
        for ident in candidates:
            self.workspace.remove(ident)
        if confirm:
            self.refresh()

    def restore_trash_selected(self):
        try:
            for ident in self.selected_ids(include_trash=True):
                self.workspace.restore(ident)
        except Exception as exc:
            self.fail(str(exc))
        self.refresh_collections()
        self.refresh()

    def delete_trash_selected(self):
        ids = self.selected_ids(include_trash=True)
        if ids and QMessageBox.question(self, "Endgültig löschen", f"{len(ids)} Aufnahme(n) endgültig löschen?") == QMessageBox.StandardButton.Yes:
            try:
                self.workspace.delete_trashed(ids)
            except Exception as exc:
                self.fail(str(exc))
            self.refresh_collections()
            self.refresh()

    def empty_trash_view(self):
        trash = self.workspace.root / "trash"
        size = sum(p.stat().st_size for p in trash.glob("*") if p.is_file() and not self.workspace.get(p.name.split(".")[0]))
        if QMessageBox.question(self, "Papierkorb leeren", f"Alle gelöschten Aufnahmen endgültig löschen?\n{size / (1024 * 1024):.2f} MB werden freigegeben.") == QMessageBox.StandardButton.Yes:
            try:
                self.workspace.empty_trash()
            except Exception as exc:
                self.fail(str(exc))
            self.refresh_collections()
            self.refresh()

    def restore_deleted(self):
        entries = self.workspace.trash_entries()
        if not entries:
            QMessageBox.information(self, "Wiederherstellen", "Der Papierkorb enthält keine gelöschten Aufnahmen.")
            return
        labels = [f"{row['title']} · {row['created']} · {row['id'][:6]}" for row in entries]
        label, ok = QInputDialog.getItem(self, "Aufnahme wiederherstellen", "Gelöschte Aufnahme", labels, editable=False)
        if ok:
            try:
                ident = self.workspace.restore(entries[labels.index(label)]["id"])
                self.refresh_collections()
                self.refresh()
                self.select_id(ident)
            except Exception as exc:
                self.fail(str(exc))

    def export_settings(self):
        if self.blocked("settings_export"):
            return
        path, _ = QFileDialog.getSaveFileName(self, "Einstellungen exportieren", "screenkaffee-Einstellungen.json", "JSON (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps({"format": "screenkaffee-settings", "version": 1, "settings": self.settings},
                    ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                self.fail(str(exc))

    def import_settings(self):
        if self.blocked("settings_import"):
            return
        path, _ = QFileDialog.getOpenFileName(self, "Einstellungen importieren", "", "JSON (*.json)")
        if path:
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                if data.get("format") != "screenkaffee-settings" or data.get("version") != 1:
                    raise ValueError("Unbekanntes Einstellungsformat.")
                proposed = data["settings"]
                for key, value in proposed.items():
                    if key in self.settings and type(value) is not type(self.settings[key]):
                        raise ValueError("Ungültiger Wert: " + key)
                if proposed.get("tone_mapping", "neutral") not in ("neutral", "highlights"):
                    raise ValueError("Ungültige HDR-Umrechnung.")
                if proposed.get("retention_count", 0) < 0 or proposed.get("retention_days", 0) < 0:
                    raise ValueError("Ungültige Aufbewahrungsregeln.")
                self.settings.update({key: value for key, value in proposed.items() if key in self.settings})
                self.save_settings()
                self.statusBar().showMessage("Einstellungen importiert")
            except Exception as exc:
                self.fail(str(exc))

    def reveal_selected(self):
        ident = self.require_one()
        if ident:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.workspace.path(ident).parent)))

    def remove_selected(self):
        if self.in_trash():
            return self.delete_trash_selected()
        ids = self.selected_ids()
        if not ids:
            return
        if self.settings.get("confirm_delete", True):
            answer = QMessageBox.question(self, "Aufnahmen entfernen", f"{len(ids)} Aufnahme(n) aus dem Workspace entfernen?\nDateien werden in data/trash verschoben.")
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            for ident in ids:
                self.workspace.remove(ident)
            self.refresh_collections()
            self.refresh()
        except Exception as exc:
            self.refresh_collections()
            self.refresh()
            self.fail(str(exc))

    def preferences(self):
        from PySide6.QtWidgets import QScrollArea
        dialog = QDialog(self)
        dialog.setWindowTitle("Einstellungen")
        dialog.resize(600, 560)
        outer = QVBoxLayout(dialog)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        scroll.setWidget(body)
        outer.addWidget(scroll)
        layout = QVBoxLayout(body)
        layout.addWidget(QLabel("HDR → normale PNGs (Windows)"))
        tone = QComboBox()
        tone.addItem("SDR-Farben erhalten · HDR-Spitzen abschneiden", "neutral")
        tone.addItem("Helle Details komprimieren · SDR-Weiß leicht dunkler", "highlights")
        tone.setCurrentIndex(max(0, tone.findData(self.settings["tone_mapping"])))
        layout.addWidget(tone)
        boxes = {}
        for key, label in [("auto_copy", "Neue Aufnahmen automatisch kopieren"),
            ("auto_edit", "Nach Aufnahme den Editor öffnen"), ("capture_cursor", "Mauszeiger mit aufnehmen"),
            ("minimize_to_tray", "X und Minimieren: im Infobereich neben der Uhr weiterlaufen")]:
            box = QCheckBox(label)
            box.setChecked(self.settings[key])
            boxes[key] = box
            layout.addWidget(box)
        from . import autostart
        try:
            autostart_now = autostart.is_enabled()
        except OSError:
            autostart_now = False
        autostart_box = QCheckBox("Beim Anmelden automatisch starten (im Infobereich, ohne Fenster)")
        autostart_box.setChecked(autostart_now)
        layout.addWidget(autostart_box)
        hotkey_edits = {}
        if sys.platform == "win32":
            layout.addWidget(QLabel("Globale Tastenkürzel: aus der Liste wählen oder eintippen (z. B. Druck, Strg+Umschalt+S, leer = keines)"))
            presets = ["", "Druck", "Strg+Druck", "Umschalt+Druck", "Alt+Druck", "Strg+Umschalt+Druck",
                       "Strg+Umschalt+S", "Strg+Alt+S", "F9", "F10", "F11"]
            for kind, label in ACTIONS:
                row = QHBoxLayout()
                row.addWidget(QLabel(label))
                edit = QComboBox()
                edit.setEditable(True)
                edit.addItems(presets)
                edit.setCurrentText(self.settings["hotkeys"].get(kind, DEFAULT_HOTKEYS.get(kind, "")))
                hotkey_edits[kind] = edit
                row.addWidget(edit, 1)
                layout.addLayout(row)
            hint = QLabel("Tipp für die Druck-Taste: Falls Windows sie schon nutzt, unter Windows-Einstellungen → "
                          "Barrierefreiheit → Tastatur „Bildschirmabbild-Taste: Bildschirmaufnahme öffnen“ ausschalten.")
            hint.setObjectName("muted")
            hint.setWordWrap(True)
            layout.addWidget(hint)
        layout.addWidget(QLabel("Titel neuer Aufnahmen (Datum/Zeit wie strftime, {n} = Nummer, {window}/{app}/{url} = Kontext)"))
        title_pattern = QLineEdit(self.settings["title_pattern"])
        layout.addWidget(title_pattern)
        from PySide6.QtWidgets import QFormLayout
        option_form = QFormLayout()
        export_format = QComboBox()
        for suffix in CAPTURE_FORMATS:
            export_format.addItem(suffix[1:].upper(), suffix)
        export_format.setCurrentIndex(max(0, export_format.findData(self.settings["export_format"])))
        option_form.addRow("Standardformat beim Exportieren", export_format)
        number_fields = {}
        for key, label, low, high, suffix in [("auto_scale_percent", "Neue Aufnahmen verkleinern/vergrößern", 5, 400, " %"),
                ("auto_border", "Rahmen um neue Aufnahmen", 0, 100, " px"), ("auto_shadow", "Schatten um neue Aufnahmen", 0, 200, " px"),
                ("audio_noise_gate_db", "Rauschfilter für das Mikrofon (0 = aus)", -90, 0, " dB"),
                ("audio_timeshift_ms", "Audio-Zeitversatz zum Bild", -2000, 2000, " ms"),
                ("audio_loopback_balance", "Systemton relativ zum Mikrofon", 10, 100, " %"),
                ("print_margin_mm", "Druckrand", 0, 50, " mm"), ("animation_fps", "Animierte Ausgabe: Bilder pro Sekunde", 1, 60, ""),
                ("animation_loops", "Animierte Ausgabe: Wiederholungen (0 = endlos)", 0, 1000, "")]:
            field = QSpinBox()
            field.setRange(low, high)
            field.setSuffix(suffix)
            field.setValue(self.settings[key])
            number_fields[key] = field
            option_form.addRow(label, field)
        auto_watermark = QLineEdit(self.settings["auto_watermark"])
        option_form.addRow("Wasserzeichen-Text für neue Aufnahmen", auto_watermark)
        layout.addLayout(option_form)
        option_boxes = {}
        for key, label in [("clipboard_only", "Aufnahmen nur in die Zwischenablage legen (nicht speichern)"),
                           ("auto_print", "Neue Aufnahmen automatisch drucken"), ("confirm_delete", "Vor dem Entfernen nachfragen"),
                           ("auto_trim", "Einfarbige Ränder neuer Aufnahmen automatisch abschneiden"),
                           ("follow_mouse_screen", "Nur den Bildschirm unter dem Mauszeiger aufnehmen"),
                           ("capture_context", "Aufnahmekontext (aktives Fenster, Programm und URL) in Metadaten speichern")]:
            box = QCheckBox(label)
            box.setChecked(bool(self.settings.get(key, True if key == "capture_context" else False)))
            option_boxes[key] = box
            layout.addWidget(box)
        layout.addWidget(QLabel("Video-Audioquelle (optional; Windows: exakter DirectShow-Gerätename, Linux: PulseAudio-Quelle)"))
        microphone = QLineEdit(self.settings["microphone"])
        microphone.setPlaceholderText("Leer = ohne Ton")
        layout.addWidget(microphone)
        from . import updater
        layout.addWidget(QLabel("Updates aus GitHub (Windows x64, Windows ARM64 und Linux)"))
        update_mode = QComboBox()
        for value, label in updater.MODES:
            update_mode.addItem(label, value)
        update_mode.setCurrentIndex(max(0, update_mode.findData(self.settings.get("update_mode", "off"))))
        layout.addWidget(update_mode)
        keep_previous = QCheckBox("Vorige Version nach einem Update aufbewahren (zum Wiederherstellen, braucht ca. 0,7 GB)")
        keep_previous.setChecked(self.settings.get("update_keep_previous", True))
        layout.addWidget(keep_previous)
        check_now = QPushButton("Jetzt nach Updates suchen")
        check_now.clicked.connect(lambda: self.check_updates(manual=True))
        layout.addWidget(check_now, 0, Qt.AlignmentFlag.AlignLeft)
        speech_row = QHBoxLayout()
        speech_label = QLabel(self.speech_status_text())
        speech_label.setWordWrap(True)
        speech_label.setObjectName("muted")
        speech_row.addWidget(speech_label, 1)
        from . import addons
        if addons.speech_status(None, self.workspace.root) in ("installed", "cached"):
            remove_speech = QPushButton("Entfernen …")
            remove_speech.clicked.connect(lambda: self.remove_speech_addon(speech_label))
            speech_row.addWidget(remove_speech)
        layout.addLayout(speech_row)
        note = QLabel(f"Datenordner: {self.workspace.root}\n\nLinux-HDR wird vom Compositor verarbeitet und ist auf Omarchy noch zu prüfen. Video ist derzeit SDR.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        outer.addWidget(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            capture_values = {"title_pattern": title_pattern.text(), "export_format": export_format.currentData(),
                              "auto_watermark": auto_watermark.text(), **{key: field.value() for key, field in number_fields.items()},
                              **{key: box.isChecked() for key, box in option_boxes.items()}}
            problems = validate_capture_options(capture_values)
            if problems:
                QMessageBox.warning(self, "Einstellungen", "\n".join(problems.values()))
                return
            self.settings.update(capture_values)
            self.settings.update({key: box.isChecked() for key, box in boxes.items()})
            self.settings.update(tone_mapping=tone.currentData(), microphone=microphone.text(), update_mode=update_mode.currentData(),
                                 update_keep_previous=keep_previous.isChecked())
            if autostart_box.isChecked() != autostart_now:
                try:
                    autostart.set_enabled(autostart_box.isChecked())
                except OSError as exc:
                    QMessageBox.warning(self, "Autostart", f"Der Autostart konnte nicht geändert werden:\n{exc}")
            if hotkey_edits:
                self.settings["hotkeys"] = {kind: edit.currentText().strip() for kind, edit in hotkey_edits.items()}
                problems = self.apply_hotkeys()
                if problems:
                    QMessageBox.warning(self, "Tastenkürzel", "\n".join(problems.values()))
            self.save_settings()

    def toggle_video(self):
        if self.recording_busy:
            return
        if self.recorder.active:
            self.record_timer.stop()
            self.recording_busy = True
            self.video_button.setEnabled(False)
            self.task(self.recorder.stop, self.recording_finished, "Video wird gespeichert; Segmente bleiben bei einem Fehler erhalten …")
            return
        self.configure_video()

    def configure_video(self):
        if self.blocked("video"):
            return
        from PySide6.QtWidgets import QFormLayout, QDoubleSpinBox
        from PySide6.QtMultimedia import QMediaDevices
        dialog = QDialog(self)
        dialog.setWindowTitle("Videoaufnahme")
        form = QFormLayout(dialog)
        target = QComboBox()
        for label, value in [("Gewählter Monitor", "monitor"), ("Bereich auswählen", "region")]:
            target.addItem(label, value)
        target.addItem("Gesamter Desktop" if sys.platform == "win32" else "Auswahl über Desktop-Portal", "all" if sys.platform == "win32" else "portal")
        from .videoextras import RECORD_SIZES
        for label, size in RECORD_SIZES:
            target.addItem(f"Fester Bereich {label} (zentriert)", size)
        form.addRow("Aufnahmebereich", target)
        click_effect = QCheckBox("Mausklicks als Ring sichtbar machen")
        form.addRow(click_effect)
        from .videoextras import KEYSTROKE_MODES
        keystrokes = QComboBox()
        keystrokes.addItem("Nicht einblenden", "")
        for value, label in KEYSTROKE_MODES:
            keystrokes.addItem(label, value)
        keystrokes.setCurrentIndex(max(0, keystrokes.findData(self.settings.get("video_keystrokes", ""))))
        if sys.platform != "win32":
            keystrokes.setCurrentIndex(0)
            keystrokes.setEnabled(False)
            keystrokes.setToolTip("Unter Linux/Wayland können Programme fremde Tastendrücke nicht mitlesen.")
        else:
            keystrokes.setToolTip("„Alle Tasten“ zeigt auch Getipptes – dann wären z. B. Passwörter im Video zu sehen.")
        form.addRow("Tastendrücke einblenden", keystrokes)
        max_minutes = QSpinBox()
        max_minutes.setRange(0, 600)
        max_minutes.setSuffix(" min")
        max_minutes.setSpecialValueText("unbegrenzt")
        form.addRow("Höchstdauer", max_minutes)
        quality = QComboBox()
        for label, value in [("Standard", "standard"), ("Hoch", "high"), ("Ultra", "ultra")]:
            quality.addItem(label, value)
        quality.setCurrentIndex(1)
        form.addRow("Qualität", quality)
        fps = QSpinBox()
        fps.setRange(5, 60)
        fps.setValue(30)
        form.addRow("Bilder pro Sekunde", fps)
        microphone = QComboBox()
        microphone.setEditable(True)
        microphone.addItem("Ohne Mikrofon", "")
        microphone.addItem("Standard-Mikrofon", "default_input")
        native_audio = []
        if sys.platform == "win32":
            try:
                from .audio import input_devices
                native_audio = input_devices()
            except Exception:
                LOG.warning("Audiogeräte konnten nicht gelesen werden", exc_info=True)
            for device in native_audio:
                if not device["loopback"]:
                    microphone.addItem(device["name"], device["selector"])
        else:
            for device in QMediaDevices.audioInputs():
                microphone.addItem(device.description(), bytes(device.id()).decode(errors="replace"))
        form.addRow("Mikrofon", microphone)
        system = QComboBox()
        system.setEditable(True)
        system.addItem("Ohne Systemton", "")
        system.addItem("Standard-Systemausgabe", "default_output")
        for device in native_audio:
            if device["loopback"]:
                system.addItem(device["name"], device["selector"])
        form.addRow("Systemton-Quelle", system)
        mic_volume, system_volume = QDoubleSpinBox(), QDoubleSpinBox()
        for widget in (mic_volume, system_volume):
            widget.setRange(0, 4)
            widget.setSingleStep(.1)
            widget.setValue(1)
        form.addRow("Mikrofonlautstärke", mic_volume)
        form.addRow("Systemtonlautstärke", system_volume)
        camera = QComboBox()
        camera.addItem("Ohne Webcam", None)
        for device in QMediaDevices.videoInputs():
            camera.addItem(device.description(), bytes(device.id()).hex())
        form.addRow("Webcam", camera)
        circle = QCheckBox("Webcam als Kreis anzeigen (verschiebbar)")
        circle.setChecked(True)
        form.addRow(circle)
        mirror = QCheckBox("Webcam horizontal spiegeln")
        form.addRow(mirror)
        camera_size = QSpinBox()
        camera_size.setRange(120, 600)
        camera_size.setValue(240)
        form.addRow("Webcam-Größe", camera_size)
        camera_position = QComboBox()
        for label, value in [("Unten rechts", "bottom-right"), ("Unten links", "bottom-left"), ("Oben rechts", "top-right"), ("Oben links", "top-left")]:
            camera_position.addItem(label, value)
        form.addRow("Webcam-Position", camera_position)
        note = QLabel("Video ist derzeit SDR. Bildschirm, Ton und Webcam werden lokal aufgezeichnet.\nPause/Fortsetzen steht im Menü Mehr bereit.\nZoom an der Mausposition: Strg+Alt+↑ / ↓, zurücksetzen: Strg+Alt+Pos1.\nZoomwechsel werden während der Aufnahme erfasst und beim Speichern eingerechnet.")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Aufnahme starten")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        mic = microphone.currentData() if microphone.currentIndex() >= 0 else microphone.currentText()
        system_source = system.currentData() if system.currentIndex() >= 0 else system.currentText()
        self.pending_video_options = {"fps": fps.value(), "quality": quality.currentData(), "microphone": mic or None,
            "system_audio": system_source or None, "microphone_volume": mic_volume.value(), "system_volume": system_volume.value(),
            "camera": camera.currentData(), "circle": circle.isChecked(), "camera_mirror": mirror.isChecked(),
            "camera_size": camera_size.value(), "camera_position": camera_position.currentData(), "portal": target.currentData() == "portal",
            "click_effect": click_effect.isChecked(), "keystrokes": keystrokes.currentData() or "", "max_minutes": max_minutes.value(),
            "audio_noise_gate_db": self.settings.get("audio_noise_gate_db", 0), "audio_timeshift_ms": self.settings.get("audio_timeshift_ms", 0),
            "audio_loopback_balance": self.settings.get("audio_loopback_balance", 100) / 100}
        if target.currentData() == "region":
            self.capture("video")
            return
        region = None
        if isinstance(target.currentData(), tuple):
            from .videoextras import preset_region
            name = self.screen_combo.currentData()
            screen = next((s for s in QApplication.screens() if s.name() == name), QApplication.primaryScreen())
            geometry = screen.geometry()
            scale = screen.devicePixelRatio() if sys.platform == "win32" else 1
            region = preset_region([geometry.x(), geometry.y(), round(geometry.width() * scale), round(geometry.height() * scale)], target.currentData())
            self.pending_video_options["portal"] = False
        elif target.currentData() == "monitor":
            name = self.screen_combo.currentData()
            screen = next((s for s in QApplication.screens() if s.name() == name), QApplication.primaryScreen())
            geometry = screen.geometry()
            scale = screen.devicePixelRatio() if sys.platform == "win32" else 1
            region = [geometry.x(), geometry.y(), round(geometry.width() * scale), round(geometry.height() * scale)]
        self.begin_video_recording(region)

    def begin_video_recording(self, region):
        options = dict(self.pending_video_options or {})
        camera_id = options.pop("camera", None)
        circle = options.pop("circle", True)
        mirror = options.pop("camera_mirror", False)
        camera_size = options.pop("camera_size", 240)
        camera_position = options.pop("camera_position", "bottom-right")
        click_effect = options.pop("click_effect", False)
        keystrokes = options.pop("keystrokes", "")
        max_minutes = options.pop("max_minutes", 0)
        options["region"] = region
        try:
            if camera_id:
                from PySide6.QtMultimedia import QMediaDevices
                from .webcam import WebcamOverlay
                device = next((d for d in QMediaDevices.videoInputs() if bytes(d.id()).hex() == camera_id), None)
                if device is None:
                    raise RuntimeError("Die gewählte Kamera ist nicht mehr verfügbar.")
                self.camera_overlay = WebcamOverlay(device, circle=circle, mirror=mirror, size=camera_size, position=camera_position)
                self.camera_overlay.start()
            self.recorder.start(self.workspace.root / "videos", **options)
            if click_effect:
                from .videoextras import ClickEffect
                self.click_effect = ClickEffect()
                self.click_effect.start()
            if keystrokes:
                self.start_keystroke_overlay(region, keystrokes)
            if max_minutes:
                self.stop_timer = QTimer(self)
                self.stop_timer.setSingleShot(True)
                self.stop_timer.timeout.connect(self.stop_recording_by_limit)
                self.stop_timer.start(int(max_minutes) * 60_000)
            self.video_zoom_factor = 1
            self.video_button.setText("■ Aufnahme stoppen")
            self.record_timer.start(500)
            self.statusBar().showMessage("Videoaufnahme läuft")
            self.restore()
        except Exception as exc:
            if self.camera_overlay:
                self.camera_overlay.close()
                self.camera_overlay = None
            self.fail(str(exc))

    def start_keystroke_overlay(self, region, mode):
        """Pressed keys bottom centre of the recorded area (logical coordinates for Qt)."""
        from .videoextras import KeystrokeOverlay
        self.settings["video_keystrokes"] = mode
        self.save_settings()
        screen = self.screen_for_region(region) or QApplication.primaryScreen()
        geometry = screen.geometry()
        if region:
            scale = screen.devicePixelRatio() if sys.platform == "win32" else 1
            x, y, width, height = region
            area = QRect(round(geometry.x() + (x - geometry.x()) / scale), round(geometry.y() + (y - geometry.y()) / scale),
                         round(width / scale), round(height / scale))
        else:
            area = geometry
        self.keystroke_overlay = KeystrokeOverlay(area, mode)
        self.keystroke_overlay.start()
        return self.keystroke_overlay

    def end_recording_extras(self):
        """Stop the click ring, the key display and the maximum-duration timer."""
        keys = getattr(self, "keystroke_overlay", None)
        if keys is not None:
            keys.stop()
            keys.deleteLater()
            self.keystroke_overlay = None
        effect = getattr(self, "click_effect", None)
        if effect is not None:
            effect.stop()
            effect.deleteLater()
            self.click_effect = None
        frame = getattr(self, "zoom_frame", None)
        if frame is not None:
            frame.hide()
            frame.deleteLater()
            self.zoom_frame = None
        preview = getattr(self, "zoom_preview", None)
        if preview is not None:
            preview.hide()
            preview.deleteLater()
            self.zoom_preview = None
        limit = getattr(self, "stop_timer", None)
        if limit is not None:
            limit.stop()
            self.stop_timer = None

    def stop_recording_by_limit(self):
        if self.recorder.active and not self.recording_busy:
            self.statusBar().showMessage("Höchstdauer erreicht – Aufnahme wird gespeichert")
            self.toggle_video()

    def recording_finished(self, path):
        self.end_recording_extras()
        self.recording_busy = False
        self.video_button.setEnabled(True)
        self.video_button.setText("● Video")
        if self.camera_overlay:
            self.camera_overlay.close()
            self.camera_overlay = None
        if path:
            self.import_video_path(str(path))
        if self.exit_requested:
            QTimer.singleShot(250, self.finish_exit_when_idle)

    def finish_exit_when_idle(self):
        if self.tasks:
            QTimer.singleShot(250, self.finish_exit_when_idle)
        else:
            self.quit_app()

    # --- Schritt-Anleitung ---------------------------------------------------------------
    def toggle_step_guide(self):
        if self.step_session is not None:
            self.step_session.finish()
            return None
        from . import steps
        if not steps.supported():
            QMessageBox.information(self, "Schritt-Anleitung",
                                    "Die Schritt-Anleitung braucht Mausklicks aus anderen Programmen. Das erlaubt nur Windows; "
                                    "unter Linux/Wayland ist das technisch nicht möglich.")
            return None
        if self.timelapse is not None or self.recorder.active:
            self.fail("Bitte zuerst Video oder Zeitraffer beenden.")
            return None
        self.step_session = steps.StepSession(self.step_guide_finished, self.step_guide_cancelled)
        self.step_session.start()
        self.step_guide_action.setText("Schritt-Anleitung beenden")
        if self.tray.isVisible():
            self.hide()
            self.tray.showMessage("screenkaffee", "Schritt-Anleitung läuft: Klicken Sie sich durch den Ablauf. "
                                  "Jeder Klick wird ein Schritt. Beenden mit „Fertig“ oben am Bildschirm.")
        else:
            self.showMinimized()
        return self.step_session

    def step_guide_cancelled(self):
        self.step_session = None
        self.step_guide_action.setText("Schritt-Anleitung aufnehmen …")
        self.restore()
        self.statusBar().showMessage("Schritt-Anleitung abgebrochen")

    def step_guide_finished(self, recorded, open_designer=True):
        """Schritte als Aufnahmen in einer eigenen Sammlung ablegen und den Dokument-Designer damit öffnen."""
        from .steps import collection_name, describe
        self.step_session = None
        self.step_guide_action.setText("Schritt-Anleitung aufnehmen …")
        self.restore()
        if not recorded:
            self.statusBar().showMessage("Schritt-Anleitung beendet – es wurde kein Klick festgehalten")
            return []
        collection = collection_name()
        self.workspace.create_collection(collection)
        entries, ids = [], []
        for number, step in enumerate(recorded, 1):
            title = f"Schritt {number}"
            ids.append(self.workspace.add(step.image, title=title, collection=collection,
                                          metadata={"step_guide": True, "click": list(step.position), "window": step.window}))
            entries.append((title, step.image, describe(step, number)))
        self.refresh_collections()
        self.refresh()
        self.statusBar().showMessage(f"Schritt-Anleitung: {len(recorded)} Schritte in der Sammlung „{collection}“")
        if open_designer:
            try:
                from .document_designer import DocumentDesigner
                DocumentDesigner(entries, self.workspace.root, self).exec()
            except Exception as exc:
                self.fail(str(exc))
        return ids

    # --- Live-Zeichnen ---------------------------------------------------------------------
    def toggle_live_draw(self):
        if self.live_draw is not None:
            self.live_draw.stop()
            return None
        from .livedraw import LiveDrawSession
        self.live_draw = LiveDrawSession(hold=float(self.settings.get("live_draw_hold", 3)), on_end=self.live_draw_ended)
        self.live_draw.start()
        self.live_draw_action.setText("Live-Zeichnen beenden")
        return self.live_draw

    def live_draw_ended(self):
        if self.live_draw is not None:
            self.settings["live_draw_hold"] = self.live_draw.state.hold
            self.save_settings()
        self.live_draw = None
        self.live_draw_action.setText("Live-Zeichnen auf dem Bildschirm")

    # --- Befehlssuche ------------------------------------------------------------------------
    def command_entries(self):
        from .palette import collect
        extras = getattr(self, "_palette_extras", None)
        if extras is None:
            extras = []
            for label, callback in (("Bereich aufnehmen", lambda: self.capture("region")), ("Bildschirm aufnehmen", lambda: self.capture("screen")),
                                    ("Fenster aufnehmen", lambda: self.capture("window")), ("Bearbeiten (Editor öffnen)", self.edit_selected),
                                    ("Exportieren …", self.export_selected), ("Dokument erstellen …", self.document_export),
                                    ("Einstellungen …", self.preferences)):
                action = QAction(label, self)
                action.triggered.connect(lambda _=False, c=callback: c())
                extras.append(action)
            self._palette_extras = extras
        return [(action.text(), action) for action in extras] + collect(self.more_menu)

    def open_command_palette(self):
        from .palette import CommandPalette
        dialog = CommandPalette(self.command_entries(), self)
        if self.isVisible():
            geometry = self.geometry()
            dialog.move(geometry.center().x() - dialog.width() // 2, geometry.top() + 90)
        if dialog.exec() and dialog.chosen is not None:
            QTimer.singleShot(0, dialog.chosen.trigger)
        return dialog

    # --- Aufräumen ---------------------------------------------------------------------------
    def cleanup_library(self):
        from .cleanup import scan, cleanup_dialog

        def show(report):
            cleanup_dialog(self, self.workspace, report, on_change=lambda: (self.refresh_collections(), self.refresh())).exec()
        self.task(lambda: scan(self.workspace), show, "Aufnahmen werden geprüft …")

    def toggle_timelapse(self):
        if self.timelapse is not None:
            self.stop_timelapse()
        else:
            self.configure_timelapse()

    def configure_timelapse(self):
        if self.blocked("video"):
            return None
        from PySide6.QtWidgets import QFormLayout
        from .timelapse import describe
        dialog = QDialog(self)
        dialog.setWindowTitle("Zeitraffer")
        form = QFormLayout(dialog)
        dialog.target = QComboBox()
        dialog.target.addItem("Gewählter Monitor", "monitor")
        dialog.target.addItem("Bereich auswählen", "region")
        if self.settings.get("last_region") and self.settings.get("last_region_platform") == sys.platform:
            dialog.target.addItem("Zuletzt aufgenommener Bereich", "previous")
        form.addRow("Aufnahmebereich", dialog.target)
        dialog.interval = QSpinBox()
        dialog.interval.setRange(1, 600)
        dialog.interval.setSuffix(" s")
        dialog.interval.setValue(int(self.settings.get("timelapse_interval", 5)))
        form.addRow("Ein Bild alle", dialog.interval)
        dialog.fps = QSpinBox()
        dialog.fps.setRange(5, 60)
        dialog.fps.setValue(int(self.settings.get("timelapse_fps", 30)))
        form.addRow("Bilder pro Sekunde im Video", dialog.fps)
        dialog.max_minutes = QSpinBox()
        dialog.max_minutes.setRange(0, 24 * 60)
        dialog.max_minutes.setSuffix(" min")
        dialog.max_minutes.setSpecialValueText("unbegrenzt")
        dialog.max_minutes.setValue(int(self.settings.get("timelapse_max_minutes", 0)))
        form.addRow("Höchstdauer", dialog.max_minutes)
        dialog.cursor = QCheckBox("Mauszeiger mit aufnehmen")
        dialog.cursor.setChecked(bool(self.settings.get("capture_cursor")))
        form.addRow(dialog.cursor)
        dialog.preview = QLabel()
        dialog.preview.setObjectName("muted")
        update = lambda *_: dialog.preview.setText(describe(dialog.interval.value(), dialog.fps.value()))
        dialog.interval.valueChanged.connect(update)
        dialog.fps.valueChanged.connect(update)
        update()
        form.addRow(dialog.preview)
        note = QLabel("Stoppen über das Tray-Symbol, „Mehr → Zeitraffer stoppen“ oder das Tastenkürzel. "
                      "Das Video erscheint danach im Arbeitsbereich.")
        note.setWordWrap(True)
        form.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Zeitraffer starten")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        dialog.accepted.connect(lambda: self.timelapse_chosen(dialog))
        dialog.finished.connect(dialog.deleteLater)
        dialog.open()
        return dialog

    def timelapse_chosen(self, dialog):
        self.settings.update(timelapse_interval=dialog.interval.value(), timelapse_fps=dialog.fps.value(),
                             timelapse_max_minutes=dialog.max_minutes.value())
        self.save_settings()
        self.pending_timelapse = {"interval": dialog.interval.value(), "fps": dialog.fps.value(),
                                  "max_seconds": dialog.max_minutes.value() * 60, "cursor": dialog.cursor.isChecked()}
        target = dialog.target.currentData()
        if target == "region":
            self.capture("timelapse")
        elif target == "previous":
            self.begin_timelapse(self.settings.get("last_region"))
        else:
            screen = next((s for s in QApplication.screens() if s.name() == self.screen_combo.currentData()), QApplication.primaryScreen())
            geometry = screen.geometry()
            scale = screen.devicePixelRatio() if sys.platform == "win32" else 1
            self.begin_timelapse([geometry.x(), geometry.y(), round(geometry.width() * scale), round(geometry.height() * scale)])

    def begin_timelapse(self, region, grab=None):
        options = dict(getattr(self, "pending_timelapse", None) or {})
        if not region:
            self.fail("Kein Bereich für den Zeitraffer ausgewählt.")
            return None
        from .timelapse import Timelapse
        if grab is None:
            from .capture import capture_region
            tone, cursor = self.settings["tone_mapping"], options.get("cursor", False)
            grab = lambda: capture_region(region, tone, cursor)[0]
        try:
            self.timelapse = Timelapse(self.workspace.root / "videos", grab, options.get("interval", 5),
                                       options.get("fps", 30), options.get("max_seconds", 0))
            self.timelapse.start()
        except Exception as exc:
            self.timelapse = None
            self.fail(str(exc))
            return None
        self.timelapse_action.setText("Zeitraffer stoppen")
        self.timelapse_timer.start(1000)
        self.statusBar().showMessage("Zeitraffer läuft")
        if self.tray.isVisible():
            self.hide()
            self.tray.showMessage("screenkaffee", "Zeitraffer läuft. Stoppen über das Tray-Symbol oder „Mehr → Zeitraffer stoppen“.")
        else:
            self.showMinimized()
        return self.timelapse

    def check_timelapse(self):
        lapse = self.timelapse
        if lapse is None:
            self.timelapse_timer.stop()
            return
        if not lapse.active:
            self.stop_timelapse()
            return
        minutes, seconds = divmod(int(lapse.elapsed()), 60)
        text = f"Zeitraffer läuft · {lapse.frames} Bilder · {minutes}:{seconds:02d}"
        self.statusBar().showMessage(text)
        self.tray.setToolTip("screenkaffee · " + text)

    def stop_timelapse(self):
        lapse, self.timelapse = self.timelapse, None
        self.timelapse_timer.stop()
        self.timelapse_action.setText("Zeitraffer starten …")
        self.tray.setToolTip("screenkaffee")
        if lapse is None:
            return
        reason = lapse.finished_reason

        def done(path):
            self.import_video_path(str(path))
            note = f" ({reason})" if reason else ""
            self.statusBar().showMessage(f"Zeitraffer gespeichert: {lapse.frames} Bilder{note}")
            if self.exit_requested:
                QTimer.singleShot(250, self.finish_exit_when_idle)
        if not self.exit_requested:
            self.restore()
        self.task(lapse.render, done, "Zeitraffer wird erstellt …")

    def pause_video(self):
        if self.recording_busy or not self.recorder.active:
            return
        self.record_timer.stop()
        self.recording_busy = True
        self.video_button.setEnabled(False)
        operation = self.recorder.resume if self.recorder.paused else self.recorder.pause
        self.task(operation, self.pause_finished, "Videoaufnahme wird pausiert / fortgesetzt …")

    def pause_finished(self, result):
        self.recording_busy = False
        self.video_button.setEnabled(True)
        if self.recorder.paused:
            self.video_button.setText("■ Pausierte Aufnahme speichern")
            self.statusBar().showMessage("Video pausiert · Mehr → Video pausieren / fortsetzen")
        else:
            self.video_button.setText("■ Aufnahme stoppen")
            self.record_timer.start(500)

    def check_recording(self):
        error = self.recorder.error_if_stopped()
        if error:
            self.record_timer.stop()
            self.end_recording_extras()
            self.video_button.setText("● Video")
            if self.camera_overlay:
                self.camera_overlay.close()
                self.camera_overlay = None
            self.fail(error)

    # --- Updates aus GitHub (updater.py) ---------------------------------------------------------------------------

    def startup_update(self):
        """Erster Start nach einem Update: zeigen, was neu ist. Danach je nach Einstellung nach Updates suchen."""
        from . import updater
        updater.forget_token(self.workspace.root)
        from . import updater
        try:
            finished = updater.finish_previous(keep_previous=self.settings.get("update_keep_previous", True))
        except OSError:
            finished = None
        if finished is not None:
            self.show_update_result(finished)
        elif not self.settings.get("setup_done") and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            self.first_run_setup()
        if not self.settings.get("tour_done") and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            self.schedule_tour()
        mode = self.settings.get("update_mode", "off")
        if mode in ("ask", "auto") and "update" not in self.policy["disabled"]:
            from shiboken6 import isValid
            QTimer.singleShot(3000, lambda: self.check_updates(auto=mode == "auto", quiet=True) if isValid(self) else None)

    # --- Tour ----------------------------------------------------------------------------------------
    def schedule_tour(self):
        """Tour einmalig zeigen: nach dem Einrichtungsdialog bzw. kurz nach dem Start."""
        from shiboken6 import isValid
        setup = getattr(self, "setup_dialog", None)
        if setup is not None and isValid(setup) and setup.isVisible():
            setup.finished.connect(lambda _result: QTimer.singleShot(300, lambda: self.start_tour() if isValid(self) else None))
        else:
            QTimer.singleShot(900, lambda: self.start_tour() if isValid(self) and self.isVisible() else None)

    def start_tour(self):
        from shiboken6 import isValid
        from .tour import TourOverlay, steps
        current = getattr(self, "tour", None)
        if current is not None and isValid(current):
            current.show_step(0)
            return current
        if self.isMinimized() or not self.isVisible():
            self.restore()

        def done(_completed):
            self.settings["tour_done"] = True          # auch „Tour beenden“ zählt; erneut über das Menü
            self.save_settings()
            self.tour = None
        self.tour = TourOverlay(self, steps(self.settings))
        self.tour.finished.connect(done)
        return self.tour

    def show_update_result(self, info):
        if info.get("error"):
            QMessageBox.warning(self, "Update", "Das Update konnte nicht installiert werden; die bisherige Version läuft "
                                "unverändert weiter.\n\n" + info["error"])
            return None
        from . import __version__
        from PySide6.QtWidgets import QTextBrowser
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Neu in screenkaffee {__version__}")
        dialog.resize(620, 480)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"screenkaffee wurde auf Version {__version__} aktualisiert. Das ist neu:"))
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setMarkdown(info.get("notes") or "Für diese Version gibt es keine Release-Notizen.")
        layout.addWidget(browser)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        self.whats_new = dialog
        dialog.show()
        return dialog

    def check_updates(self, manual=False, auto=False, quiet=False):
        if manual and self.blocked("update"):
            return
        if self.update_busy or self.pending_update:
            self.statusBar().showMessage("Ein Update wird bereits geladen oder wartet auf den Neustart.", 8000)
            return
        from . import updater
        repo = self.settings.get("update_repo") or updater.REPOSITORY
        def operation():
            try:
                return updater.latest_release(repo)
            except updater.UpdateError as exc:
                return exc
        self.update_busy = True
        self.task(operation, lambda result: self.update_checked(result, auto, quiet), "Suche nach Updates …")

    def update_checked(self, result, auto=False, quiet=False):
        from . import __version__, updater
        self.update_busy = False
        if isinstance(result, Exception):
            if quiet:
                self.statusBar().showMessage("Update-Prüfung: " + str(result), 15000)
            else:
                QMessageBox.warning(self, "Updates", str(result))
            return
        if result.version <= updater.current_version():
            self.show_version()
            self.statusBar().showMessage(f"screenkaffee {__version__} ist aktuell.", 8000)
            if not quiet:
                QMessageBox.information(self, "Updates", f"screenkaffee {__version__} ist die neueste Version.")
            return
        self.show_version(result.tag)
        variant = updater.installed_variant()
        if variant is None:
            text = f"screenkaffee {result.tag} ist verfügbar. Diese Quellcode-Version wird mit „git pull“ aktualisiert."
            self.statusBar().showMessage(text, 15000)
            if not quiet:
                QMessageBox.information(self, "Updates", f"{text}\n\n{result.url}")
            return
        if not auto:
            size = result.assets.get(updater.ASSETS[variant], {}).get("size", 0)
            box = QMessageBox(QMessageBox.Icon.Question, "Update verfügbar",
                              f"screenkaffee {result.tag} ist verfügbar (installiert: {__version__}, Download "
                              f"{size / 1e6:.0f} MB).\n\nJetzt herunterladen und installieren? screenkaffee startet "
                              "dafür einmal neu; Aufnahmen und Einstellungen bleiben erhalten.",
                              QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
            box.setDetailedText(result.notes or "Keine Release-Notizen.")
            if box.exec() != QMessageBox.StandardButton.Yes:
                return
        self.download_update(result, variant, auto)

    def prepare_speech(self, parent, then):
        """True, wenn die Untertitel-Erkennung bereit ist; sonst Download anbieten und danach `then()` aufrufen."""
        from . import addons, updater
        try:
            if addons.ensure_speech(None, self.workspace.root):
                return True
        except OSError as exc:
            QMessageBox.warning(parent, "Untertitel", f"Die Untertitel-Erweiterung konnte nicht eingerichtet werden:\n{exc}")
            return False
        if addons.speech_status(None, self.workspace.root) == "unsupported":
            QMessageBox.information(parent, "Untertitel", "Die lokale Untertitel-Erkennung gibt es für dieses Paket nicht "
                                    "(Windows ARM64). SRT-Dateien lassen sich weiterhin öffnen, bearbeiten und einbetten.")
            return False
        if getattr(self, "speech_download", None) is not None:
            QMessageBox.information(parent, "Untertitel", "Die Untertitel-Erweiterung wird gerade geladen.")
            return False
        answer = QMessageBox.question(parent, "Untertitel-Erweiterung laden?",
            "Die lokale Untertitel-Erkennung (Whisper) ist eine optionale Erweiterung "
            f"({addons.SPEECH_SIZE_HINT}). Sie wird einmal aus dem GitHub-Release dieser Version geladen, "
            "per SHA256 geprüft und im Datenordner abgelegt. Die Erkennung selbst läuft danach komplett lokal.\n\n"
            "Jetzt herunterladen?")
        if answer != QMessageBox.StandardButton.Yes:
            return False
        from .progress import Cancelled
        repo = self.settings.get("update_repo") or updater.REPOSITORY
        progress, window = self.download_window("Untertitel-Erweiterung", "Untertitel-Erweiterung wird geladen", parent)
        self.speech_download = progress.state
        self.speech_window = window
        def operation():
            try:
                return addons.install_speech(self.workspace.root, repo=repo, progress=progress.report, stage=progress.stage)
            except (addons.AddonError, updater.UpdateError, OSError, ValueError, Cancelled) as exc:
                return exc
        def finished(result):
            self.speech_download = None
            from shiboken6 import isValid
            target = parent if isValid(parent) else self
            if isinstance(result, Cancelled):
                if isValid(window):
                    window.finish("Abgebrochen. Die Erweiterung lässt sich später erneut laden.", success=False)
                self.statusBar().showMessage("Untertitel-Erweiterung abgebrochen", 8000)
                return
            if isinstance(result, Exception):
                if isValid(window):
                    window.finish("Die Untertitel-Erweiterung konnte nicht geladen werden.", success=False)
                self.statusBar().showMessage("Untertitel-Erweiterung nicht geladen")
                QMessageBox.warning(target, "Untertitel", f"Die Untertitel-Erweiterung konnte nicht geladen werden:\n{result}")
                return
            if isValid(window):
                window.finish("Fertig – die Untertitel-Erweiterung ist eingerichtet.")
            self.statusBar().showMessage("Untertitel-Erweiterung installiert", 10000)
            if isValid(parent):
                then()
        self.task(operation, finished, "Untertitel-Erweiterung wird geladen und geprüft …")
        return False

    def remove_speech_addon(self, label=None):
        from . import addons
        if QMessageBox.question(self, "Untertitel-Erweiterung", "Die Untertitel-Erweiterung entfernen? "
                                "Sie lässt sich jederzeit wieder laden.") != QMessageBox.StandardButton.Yes:
            return 0
        freed = addons.remove_speech(self.workspace.root)
        if label is not None:
            label.setText(self.speech_status_text())
        self.statusBar().showMessage(f"Untertitel-Erweiterung entfernt ({freed / 1e6:.0f} MB frei)")
        return freed

    def speech_status_text(self):
        from . import addons
        status = addons.speech_status(None, self.workspace.root)
        return {"builtin": "Untertitel-Erkennung: in dieser Quellcode-Version direkt verfügbar.",
                "installed": "Untertitel-Erweiterung: installiert.",
                "cached": "Untertitel-Erweiterung: im Datenordner vorhanden, wird beim nächsten Gebrauch eingerichtet.",
                "missing": f"Untertitel-Erweiterung: nicht installiert ({addons.SPEECH_SIZE_HINT}) – wird im Video-Editor "
                           "bei „Untertitel lokal erzeugen“ angeboten.",
                "unsupported": "Untertitel-Erkennung: für dieses Paket nicht verfügbar."}[status]

    def show_version(self, available=None):
        """Version unten rechts; mit `available` zusätzlich der Hinweis auf ein neueres Release."""
        from . import __version__
        if available:
            self.version_button.setText(f"Version {__version__} · Update {available.lstrip('v')} verfügbar")
            self.version_button.setToolTip("Klicken, um das Update zu laden")
        else:
            self.version_button.setText(f"Version {__version__}")
            self.version_button.setToolTip("Installierte Version – klicken, um nach Updates zu suchen")

    def download_window(self, title, heading, parent=None):
        """Fortschrittsfenster + Zustand für einen Download in zwei Schritten (laden/prüfen, entpacken)."""
        from .progress import Progress, ProgressWindow
        progress = Progress([("download", 0.85, "Herunterladen und prüfen"), ("extract", 0.15, "Entpacken")])
        window = ProgressWindow(title, heading, progress, parent or self)
        timer = QTimer(window)
        timer.timeout.connect(lambda: self.statusBar().showMessage(window.status_line())
                              if window.isHidden() and window.finished_text is None else None)
        timer.start(1000)
        window.show()
        return progress, window

    def download_update(self, release, variant, auto=False):
        from . import updater
        from .progress import Cancelled
        progress, window = self.download_window("Update", f"screenkaffee {release.tag.lstrip('v')} wird geladen")
        self.update_window = window
        def operation():
            try:
                return updater.prepare(release, variant, progress=progress.report, stage=progress.stage)
            except (updater.UpdateError, OSError, Cancelled) as exc:
                return exc
        self.update_busy = True
        self.task(operation, lambda result: self.update_downloaded(result, release, variant, auto),
                  f"Update {release.tag} wird geladen und geprüft …")

    def update_downloaded(self, result, release, variant, auto=False):
        from .progress import Cancelled
        self.update_busy = False
        window = getattr(self, "update_window", None)
        if isinstance(result, Cancelled):
            if window is not None:
                window.finish("Update abgebrochen. Es wurde nichts verändert.", success=False)
            self.statusBar().showMessage("Update abgebrochen", 8000)
            return
        if isinstance(result, Exception):
            if window is not None:
                window.finish("Das Update konnte nicht geladen werden.", success=False)
            if auto:
                self.statusBar().showMessage("Update fehlgeschlagen: " + str(result), 15000)
            else:
                QMessageBox.warning(self, "Updates", f"Das Update konnte nicht vorbereitet werden:\n{result}")
            return
        self.pending_update = (result, variant, release.tag)
        if window is not None:
            window.finish("Fertig geladen und geprüft. screenkaffee startet gleich neu und installiert das Update …")
            window.show()
        QTimer.singleShot(500, self.restart_for_update)

    def restart_for_update(self, attempts=20):
        """Neustart sofort, wenn nichts läuft; sonst wird das Update beim Beenden installiert."""
        if not self.pending_update:
            return
        tag = self.pending_update[2]
        busy = (self.capture_pending or self.editors or self.recorder.active or self.timelapse is not None
                or self.step_session is not None)
        if self.tasks and attempts and not busy:
            QTimer.singleShot(500, lambda: self.restart_for_update(attempts - 1))     # z. B. der Download-Thread endet gerade
            return
        if self.tasks or busy:
            text = f"Update {tag} ist bereit und wird beim Beenden von screenkaffee installiert."
            window = getattr(self, "update_window", None)
            if window is not None and window.finished_text is not None:
                window.finish(text + " Gerade ist noch ein Editor, eine Aufnahme oder ein Video offen.")
            self.statusBar().showMessage(text, 30000)
            if self.tray.isVisible():
                self.tray.showMessage("screenkaffee", text)
            return
        self.statusBar().showMessage(f"Update {tag} wird installiert, screenkaffee startet gleich neu …")
        self.quit_app()

    def restore_previous_version(self):
        if self.blocked("update"):
            return
        from . import __version__, updater
        info = updater.previous_version()
        if info is None:
            QMessageBox.information(self, "Vorige Version", "Es ist keine vorige Version vorhanden. screenkaffee "
                                    "bewahrt sie beim nächsten Update auf.")
            return
        answer = QMessageBox.question(self, "Vorige Version wiederherstellen",
            f"screenkaffee {info['version']} wiederherstellen (installiert: {__version__})?\n\nscreenkaffee startet "
            "dafür neu. Aufnahmen und Einstellungen bleiben erhalten; automatische Updates werden ausgeschaltet.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            package = updater.prepare_restore()
        except (updater.UpdateError, OSError) as exc:
            QMessageBox.warning(self, "Vorige Version", str(exc))
            return
        self.settings["update_mode"] = "off"
        self.save_settings()
        self.pending_update = (package, updater.installed_variant(), "v" + info["version"])
        self.restart_for_update()

    def capture_command(self):
        """Befehl, mit dem Hyprland & Co. eine Bereichsaufnahme auslösen."""
        from . import updater
        if updater.installed_variant() == "linux-x86_64":
            return f"{updater.application_root() / 'screenkaffee'} --capture region"
        return f"{sys.executable} -m screenkaffee --capture region"

    def first_run_setup(self):
        """Einrichtung beim ersten Start (und über „Mehr“): Aufnahmetaste, Autostart, Updates."""
        from PySide6.QtWidgets import QFormLayout
        from . import autostart, updater
        dialog = QDialog(self)
        dialog.setWindowTitle("Willkommen bei screenkaffee")
        dialog.resize(560, 0)
        layout = QVBoxLayout(dialog)
        intro = QLabel("Drei kurze Fragen, dann kann es losgehen. Alles lässt sich später unter Einstellungen ändern.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        dialog.hotkey = None
        if sys.platform == "win32":
            dialog.hotkey = QComboBox()
            dialog.hotkey.setEditable(True)
            dialog.hotkey.addItems(["Druck", "Strg+Druck", "Umschalt+Druck", "Strg+Umschalt+S", "F9", ""])
            from .hotkeys import display_hotkey
            current = self.settings["hotkeys"].get("region", "")
            dialog.hotkey.setCurrentText(display_hotkey(current) if current else "")
            form.addRow("Taste für Bereichsaufnahme", dialog.hotkey)
        else:
            binding = QLineEdit("bind = , Print, exec, " + self.capture_command())
            binding.setReadOnly(True)
            copy = QPushButton("Kopieren")
            copy.clicked.connect(lambda: QApplication.clipboard().setText(binding.text()))
            row = QHBoxLayout()
            row.addWidget(binding, 1)
            row.addWidget(copy)
            hint = QLabel("Unter Omarchy/Hyprland legst du die Aufnahmetaste in ~/.config/hypr/hyprland.conf fest:")
            hint.setWordWrap(True)
            form.addRow(hint)
            form.addRow(row)
            dialog.binding = binding
        try:
            autostart_now = autostart.is_enabled()
        except OSError:
            autostart_now = False
        dialog.autostart = QCheckBox("Beim Anmelden automatisch starten (im Infobereich, ohne Fenster)")
        dialog.autostart.setChecked(autostart_now)
        form.addRow(dialog.autostart)
        dialog.update_mode = QComboBox()
        for value, label in updater.MODES:
            dialog.update_mode.addItem(label, value)
        dialog.update_mode.setCurrentIndex(max(0, dialog.update_mode.findData(self.settings.get("update_mode", "off"))))
        form.addRow("Updates", dialog.update_mode)
        layout.addLayout(form)
        buttons = QDialogButtonBox()
        buttons.addButton("Fertig", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Später", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def accepted():
            if dialog.hotkey is not None:
                self.settings["hotkeys"]["region"] = dialog.hotkey.currentText().strip()
                problems = self.apply_hotkeys()
                if problems:
                    self.report_hotkey_problems(problems)
            if dialog.autostart.isChecked() != autostart_now:
                try:
                    autostart.set_enabled(dialog.autostart.isChecked())
                except OSError as exc:
                    QMessageBox.warning(self, "Autostart", f"Der Autostart konnte nicht geändert werden:\n{exc}")
            self.settings["update_mode"] = dialog.update_mode.currentData()

        def finished(_result):
            self.settings["setup_done"] = True      # „Später“ fragt auch nicht wieder; erneut über „Mehr“
            self.save_settings()
        dialog.accepted.connect(accepted)
        dialog.finished.connect(finished)
        self.setup_dialog = dialog
        dialog.show()
        return dialog

    def launch_pending_update(self):
        if not self.pending_update:
            return
        from . import updater
        package, variant, _ = self.pending_update
        self.pending_update = None
        root = updater.application_root()
        data = os.environ.get("SCREENKAFFEE_DATA")
        custom = data if data and Path(data).resolve() != (root / "data").resolve() else None
        try:
            updater.launch_helper(updater.helper_command(package, variant, root, os.getpid(), custom))
        except OSError:
            logging.warning("Update-Helfer konnte nicht gestartet werden", exc_info=True)

    def about(self):
        from .parity import report
        self.show_text("screenkaffee · Funktionsübersicht", report())

    def quit_app(self):
        if self.tasks or self.capture_pending:
            QMessageBox.information(self, "Vorgang läuft", "Bitte die laufende Aufnahme oder Verarbeitung zuerst abschließen.")
            return
        for editor in list(self.editors):
            if not editor.close():
                return
        if self.recorder.active:
            self.exit_requested = True
            self.toggle_video()
            return
        if self.timelapse is not None:
            self.exit_requested = True
            self.stop_timelapse()
            return
        if self.step_session is not None:
            self.step_session.cancel()
        if self.live_draw is not None:
            self.live_draw.stop()
        if self.hotkeys:
            self.hotkeys.close()
        self.tray.hide()
        self.workspace.close()
        self.launch_pending_update()
        QApplication.instance().quit()

    def closeEvent(self, event):
        event.ignore()
        if self.hide_to_tray():
            return
        self.quit_app()

    def hide_to_tray(self):
        """Never hide the only accessible window when the desktop has no tray."""
        if self.settings["minimize_to_tray"] and self.tray.isVisible():
            self.hide()
            return True
        return False

    def changeEvent(self, event):
        from PySide6.QtCore import QEvent
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            QTimer.singleShot(0, self.hide_to_tray)
