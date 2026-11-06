"""Wayland: XDG Screenshot portal. X11: Qt desktop grab.

Portal screenshots are already rendered by the compositor. Their embedded ICC
profile is honored. The portal API does not expose raw HDR or an SDR white level;
do not pretend to apply the Windows scRGB transform to these images.
"""
import os
import uuid
from urllib.parse import urlparse, unquote
from PySide6.QtCore import QObject, Slot, SLOT, QEventLoop, QTimer
from .color import import_srgb
from .i18n import tr


def portal_screenshot():
    from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusObjectPath
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        raise RuntimeError(tr("Keine D-Bus-Sitzung verfügbar."))
    token = "prism_" + uuid.uuid4().hex
    sender = bus.baseService().lstrip(":").replace(".", "_")
    predicted = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
    loop = QEventLoop()

    class Response(QObject):
        code = None
        result = None

        @Slot("uint", "QVariantMap")
        def received(self, code, result):
            self.code, self.result = code, result
            loop.quit()

    receiver = Response()
    service = "org.freedesktop.portal.Desktop"
    interface = "org.freedesktop.portal.Request"
    if not bus.connect(service, predicted, interface, "Response", receiver, SlotSignature):
        raise RuntimeError(tr("Portal-Antwort konnte nicht abonniert werden."))
    actual_path = predicted
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    try:
        message = QDBusMessage.createMethodCall(service, "/org/freedesktop/portal/desktop",
                    "org.freedesktop.portal.Screenshot", "Screenshot")
        message.setArguments(["", {"handle_token": token, "interactive": True, "modal": True}])
        reply = bus.call(message)
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            raise RuntimeError(tr("Screenshot-Portal: {error}", error=reply.errorMessage()))
        handle = reply.arguments()[0]
        actual_path = handle.path() if isinstance(handle, QDBusObjectPath) else str(handle)
        if actual_path != predicted:
            bus.connect(service, actual_path, interface, "Response", receiver, SlotSignature)
        if receiver.code is None:
            timer.start(120_000)
            loop.exec()
        if receiver.code is None:
            close = QDBusMessage.createMethodCall(service, actual_path, interface, "Close")
            bus.asyncCall(close)
            raise RuntimeError(tr("Screenshot-Auswahl hat das Zeitlimit überschritten."))
        if receiver.code == 1:
            return None
        if receiver.code != 0:
            raise RuntimeError(tr("Der Desktop hat die Screenshot-Anfrage abgelehnt."))
        uri = receiver.result.get("uri", "")
        parsed = urlparse(uri)
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
            raise RuntimeError(tr("Portal lieferte keine lokale Bilddatei."))
        return import_srgb(unquote(parsed.path))
    finally:
        timer.stop()
        for path in {predicted, actual_path}:
            bus.disconnect(service, path, interface, "Response", receiver, SlotSignature)


SlotSignature = SLOT("received(uint,QVariantMap)")


def is_wayland():
    return os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
