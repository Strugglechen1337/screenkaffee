from PIL import Image
from screenkaffee.app import MainWindow
from screenkaffee.workspace import Workspace
from PySide6.QtCore import QThread, QEventLoop, QTimer


def test_close_and_minimize_hide_to_tray(qt_app, tmp_path, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    (tmp_path / "settings.json").write_text('{"minimize_to_tray": false}', encoding="utf-8")
    workspace = Workspace(tmp_path)
    window = MainWindow(workspace)
    assert window.settings["minimize_to_tray"] is True
    monkeypatch.setattr(window.tray, "isVisible", lambda: True)
    window.show()
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert not window.isVisible()
    window.restore()
    window.showMinimized()
    qt_app.processEvents()
    assert not window.isVisible()
    window.restore()
    assert window.isVisible() and not window.isMinimized()
    monkeypatch.setattr(window.tray, "isVisible", lambda: False)
    assert not window.hide_to_tray()
    assert window.isVisible()
    exits = []
    monkeypatch.setattr(window, "quit_app", lambda: exits.append(True))
    window.closeEvent(QCloseEvent())
    assert exits == [True]
    if window.hotkeys:
        window.hotkeys.close()
        qt_app.removeNativeEventFilter(window.hotkeys)
    window.tray.hide()
    workspace.close()
    window.hide()
    window.deleteLater()


def test_workspace_ui_import_edit_render_and_search(qt_app, tmp_path):
    workspace = Workspace(tmp_path)
    ident = workspace.add(Image.new("RGB", (120, 90), "#336699"), "UI Aufnahme")
    window = MainWindow(workspace)
    window.select_id(ident)
    group = window.capture_button.parentWidget()
    assert group.objectName() == "commandGroup"
    assert group.layout().spacing() == 0
    assert [group.layout().itemAt(i).widget().text() for i in range(3)] == ["+ Bereich aufnehmen", "Bildschirm", "Fenster"]
    assert window.require_one() == ident
    assert window.rendered(ident).getpixel((20, 20)) == (51, 102, 153)
    window.open_editor(ident)
    assert len(window.editors) == 1
    assert window.editors[0].view.render_image().size == (120, 90)
    window.search.setText("fehlt")
    assert window.list.count() == 0
    window.search.clear()
    assert window.list.count() == 1
    for editor in list(window.editors):
        editor.close()
    if window.hotkeys:
        window.hotkeys.close()
        qt_app.removeNativeEventFilter(window.hotkeys)
    window.tray.hide()
    workspace.close()
    window.deleteLater()


def test_background_result_is_delivered_on_gui_thread(qt_app, tmp_path):
    workspace = Workspace(tmp_path)
    window = MainWindow(workspace)
    loop = QEventLoop()
    threads = []
    def done(value):
        threads.append(QThread.currentThread() == qt_app.thread())
    window.task(lambda: 42, done, "Test")
    timer = QTimer()
    def poll():
        if not window.tasks:
            loop.quit()
    timer.timeout.connect(poll)
    timer.start(20)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    timer.stop()
    assert threads == [True]
    assert not window.tasks
    if window.hotkeys:
        window.hotkeys.close()
        qt_app.removeNativeEventFilter(window.hotkeys)
    window.tray.hide()
    workspace.close()
    window.deleteLater()


def test_capture_skips_350ms_wait_when_delay_is_zero(qt_app, tmp_path, monkeypatch):
    workspace = Workspace(tmp_path)
    window = MainWindow(workspace)
    waits = []
    monkeypatch.setattr(QTimer, "singleShot", lambda ms, cb: waits.append(ms))
    window.hide()
    window.delay.setValue(0)
    window.capture("region")
    assert waits == [0]
    window.capture_pending = False
    window.show()
    window.capture("region")
    assert waits[-1] == 20
    if window.hotkeys:
        window.hotkeys.close()
        qt_app.removeNativeEventFilter(window.hotkeys)
    window.tray.hide()
    workspace.close()
    window.hide()
    window.deleteLater()

