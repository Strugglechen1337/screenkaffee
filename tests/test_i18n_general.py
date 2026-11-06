"""Englische Dialoge und sprachunabhängige gespeicherte Werte."""
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtWidgets import QPushButton

from screenkaffee import i18n


@pytest.fixture
def english():
    i18n.set_language("en")
    try:
        yield
    finally:
        i18n.set_language("de")


def make_video_editor(monkeypatch, tmp_path):
    from screenkaffee import media_editor
    from screenkaffee.media import MediaInfo
    monkeypatch.setattr(media_editor, "probe_media", lambda path: MediaInfo(12, 640, 360, 30, False, "h264"))
    monkeypatch.setattr(media_editor, "extract_frame", lambda *args: Image.new("RGB", (640, 360), "#427870"))
    return media_editor.VideoEditor(tmp_path / "sample.mp4")


def test_english_document_and_video_windows(qt_app, english, tmp_path, monkeypatch):
    from screenkaffee.document_designer import DocumentDesigner
    document = DocumentDesigner([("Example capture", Image.new("RGB", (640, 360), "#427870"), "Example description")], tmp_path)
    assert document.windowTitle() == "screenkaffee · Document designer"
    assert document.title.text() == "Guide"
    assert document.page_break.text() == "Page break after each capture"
    assert "Export document …" in [b.text() for b in document.findChildren(QPushButton)]
    video = make_video_editor(monkeypatch, tmp_path)
    assert video.crop_enabled.text() == "Crop image"
    assert video.apply_button.text() == "Export edited video …"
    video.job_kind = "video"
    video.operation_done(Path("edited.mp4"))
    assert video.status.text() == "Video saved: edited.mp4"
    video.cuts = [(1, 2)]
    video._refresh_cut_list()
    assert video.cut_list.count() == 1
    video.close()
    document.close()


def test_english_details_cleanup_and_collection_identity(qt_app, english, tmp_path):
    from screenkaffee.workspace import Workspace, details_dialog
    from screenkaffee.cleanup import Report, cleanup_dialog
    workspace = Workspace(tmp_path)
    ident = workspace.add(Image.new("RGB", (20, 20)), title="Example")
    details = details_dialog(None, workspace, [ident])
    assert details.windowTitle() == "Edit details"
    assert details.collection_combo.currentText() == "All captures"
    details.apply()
    assert workspace.get(ident)["collection"] == "Alle Aufnahmen"
    multiple = details_dialog(None, workspace, [ident, ident])
    assert multiple.windowTitle() == "Edit details (2 captures)"
    cleanup = cleanup_dialog(None, workspace, Report(scanned=1))
    assert cleanup.windowTitle() == "Clean up"
    assert "Empty Trash (0 B) …" in [b.text() for b in cleanup.findChildren(QPushButton)]
    for dialog in (details, multiple, cleanup):
        dialog.close()
    workspace.db.close()


def test_english_phone_page_and_dialog(qt_app, english, tmp_path, monkeypatch):
    from screenkaffee import lanshare
    page = lanshare.page([(tmp_path / "test.png", "<Example>")], "token").decode()
    assert '<html lang="en">' in page
    assert "Download</a>" in page
    assert "&lt;Example&gt;" in page
    assert "Sharing ends" in page
    monkeypatch.setattr(lanshare, "local_addresses", lambda: ["192.168.1.2"])
    class FakeShare:
        url = "http://192.168.1.2:1234/token/"
        expired = False
        downloads = visits = 0
        def __init__(self, *args, **kwargs): pass
        def start(self): return self
        def stop(self): pass
    monkeypatch.setattr(lanshare, "Share", FakeShare)
    dialog = lanshare.share_dialog(None, [(tmp_path / "test.png", "Example")])
    assert dialog.windowTitle() == "Send to phone"
    assert "Copy link" in [b.text() for b in dialog.findChildren(QPushButton)]
    assert dialog.status.text() == "Waiting for phone …"
    dialog.accept()


def test_english_keys_preserve_typing_detection(english):
    from screenkaffee.videoextras import KeyTracker
    tracker = KeyTracker(char_for=lambda vk: "")
    assert tracker.feed({0xA2, 0xA0, 0x53}, 0) == "Ctrl + Shift + S"
    tracker.feed(set(), 1)
    assert tracker.feed({0x20}, 2) is None
    tracker = KeyTracker(mode="all", char_for=lambda vk: "")
    assert tracker.feed({0x41}, 0) == "a"
    tracker.feed(set(), 1)
    assert tracker.feed({0x20}, 2) == " "


def test_english_generated_documents_and_messages(english, tmp_path):
    from screenkaffee.documents import DocumentOptions, export_html
    from screenkaffee.local_share import save_mail_draft
    from screenkaffee.cli import parse_rect
    assert DocumentOptions().title == "Captures"
    destination = export_html([("", Image.new("RGB", (20, 20)), "")], tmp_path / "guide.html")
    page = destination.read_text(encoding="utf-8")
    assert '<html lang="en">' in page
    assert "Capture 1" in page
    draft = save_mail_draft(tmp_path / "message.eml", [])
    assert b"screenkaffee captures" in draft.read_bytes()
    with pytest.raises(ValueError, match="Invalid rectangle"):
        parse_rect("invalid")


def test_english_step_bar_and_properties(qt_app, english, tmp_path):
    from types import SimpleNamespace
    from screenkaffee.steps import StepBar, describe
    from screenkaffee.media_properties import media_properties
    session = SimpleNamespace(finish=lambda: None, cancel=lambda: None)
    bar = StepBar(session)
    bar.update_count(2)
    assert bar.label.text() == "● Step guide · 2 steps · every click is captured"
    assert "Done" in [button.text() for button in bar.findChildren(QPushButton)]
    assert describe(SimpleNamespace(window="Example"), 1) == "Step 1: Click the annotated location in “Example”."
    path = tmp_path / "example.png"
    Image.new("RGB", (20, 10)).save(path)
    assert media_properties(path)["Width"] == 20
    bar.close()


def test_video_seek_fallback_uses_error_type(english, tmp_path, monkeypatch):
    from screenkaffee import media
    monkeypatch.setattr(media, "probe_media", lambda path: media.MediaInfo(10, 640, 360, 30, False))
    calls = []
    def render(args, destination, sources):
        calls.append(args)
        if len(calls) == 1:
            raise media.NoVideoStreamError(i18n.tr("Datei enthält keinen Videostream."))
        return destination
    monkeypatch.setattr(media, "_render", render)
    destination = tmp_path / "output.mp4"
    assert media.edit_video(tmp_path / "input.mp4", destination, start=1) == destination
    assert len(calls) == 2
