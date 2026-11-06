import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app


@pytest.fixture(autouse=True)
def dispose_test_windows():
    yield
    # Drain Qt's deferred destruction between tests, before Python's cycle GC
    # can tear down an old scene while a later test is dispatching paint events.
    import gc
    app = QApplication.instance()
    if app:
        from PySide6.QtCore import QCoreApplication, QEvent, QUrl
        for widget in list(app.topLevelWidgets()):
            hotkeys = getattr(widget, "hotkeys", None)
            if hotkeys is not None:
                hotkeys.close()
                app.removeNativeEventFilter(hotkeys)
                widget.hotkeys = None
            tray = getattr(widget, "tray", None)
            if tray is not None:
                tray.hide()
            player = getattr(widget, "player", None)
            if player is not None:
                player.stop()
                player.setSource(QUrl())
            view = getattr(widget, "view", None)
            if view is not None and getattr(view, "undo", None) is not None:
                view.undo.clear()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        gc.collect()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

