import json
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QFileDialog, QMenu, QMessageBox

from screenkaffee import cleanup
from screenkaffee.integrations import run, tool
from screenkaffee.media import edit_video, ffmpeg_binary, kept_segments, probe_media
from screenkaffee.media_editor import (CROP_RATIOS, VideoEditor, constrain_crop,
                                       crop_hit_test, drag_crop_rect, even_value)
from screenkaffee.workspace import Workspace, details_dialog, parse_tags


def ffprobe_duration(path):
    ffprobe = tool("ffprobe")
    if ffprobe:
        res = run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                   "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
        if res.returncode == 0 and res.stdout.strip():
            return float(res.stdout.decode().strip())
    pytest.fail("Die Dauer muss mit ffprobe gepr?ft werden.")


@pytest.fixture
def test_videos(tmp_path):
    binary = ffmpeg_binary()
    with_audio = tmp_path / "mit_ton.mp4"
    without_audio = tmp_path / "ohne_ton.mp4"
    subprocess.run([
        binary, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=d=3:s=320x180:r=20",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(with_audio)
    ], check=True, timeout=120)
    subprocess.run([
        binary, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=d=3:s=320x180:r=20",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(without_audio)
    ], check=True, timeout=120)
    return with_audio, without_audio


# --- A) Video: Bereiche aus der Mitte herausschneiden -----------------------------------------

def test_kept_segments_merges_overlaps_and_validates():
    assert kept_segments(0, 10, [(2, 4), (6, 8)], 10) == [(0.0, 2.0), (4.0, 6.0), (8.0, 10.0)]
    assert kept_segments(0, 10, [(2, 5), (4, 7)], 10) == [(0.0, 2.0), (7.0, 10.0)]
    with pytest.raises(ValueError):
        kept_segments(0, 5, [(3, 2)], 5)
    with pytest.raises(ValueError):
        kept_segments(0, 5, [(0, 5)], 5)


def test_edit_video_cuts_middle_sections_with_and_without_audio(test_videos, tmp_path):
    with_audio, without_audio = test_videos
    out_audio = tmp_path / "geschnitten_ton.mp4"
    edit_video(with_audio, out_audio, cuts=[(0.5, 1.5), (2.0, 2.5)])
    info_a = probe_media(out_audio)
    dur_a = ffprobe_duration(out_audio)
    assert info_a.has_audio
    assert 1.25 <= dur_a <= 1.75

    out_silent = tmp_path / "geschnitten_stumm.mp4"
    edit_video(without_audio, out_silent, cuts=[(1.0, 2.0)])
    info_s = probe_media(out_silent)
    dur_s = ffprobe_duration(out_silent)
    assert not info_s.has_audio
    assert 1.75 <= dur_s <= 2.25


def test_video_editor_cut_list_add_remove_and_export(qt_app, test_videos, tmp_path, monkeypatch):
    with_audio, _ = test_videos
    editor = VideoEditor(with_audio)
    monkeypatch.setattr(editor.player, "position", lambda: 600)
    editor.cut_start_button.click()
    assert editor.cut_start.value() == pytest.approx(0.6)
    monkeypatch.setattr(editor.player, "position", lambda: 1600)
    editor.cut_end_button.click()
    assert editor.cut_end.value() == pytest.approx(1.6)
    editor.add_cut_button.click()
    assert editor.cuts == [(0.6, 1.6)] and editor.cut_list.count() == 1

    editor.add_cut(2.0, 2.5)
    assert len(editor.cuts) == 2 and editor.cut_list.count() == 2
    editor.cut_list.setCurrentRow(1)
    editor.remove_cut_button.click()
    assert editor.cuts == [(0.6, 1.6)] and editor.cut_list.count() == 1

    target = tmp_path / "editor_cut.mp4"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    editor.export_video()
    editor.job.wait(60000)
    for _ in range(50):
        qt_app.processEvents()
    assert target.is_file()
    assert 1.75 <= ffprobe_duration(target) <= 2.25
    editor.close()


# --- B) Video: interaktiver Zuschnittsrahmen --------------------------------------------------

def test_crop_geometry_even_rounding_and_aspect_ratios():
    assert even_value(101) == 100 and even_value(1, minimum=2) == 2
    bounds = (320, 180)
    assert constrain_crop((11, 13, 151, 91), bounds) == (10, 12, 150, 90)
    for label, ratio in CROP_RATIOS:
        rect = constrain_crop((10, 10, 200, 140), bounds, ratio=ratio)
        assert all(v % 2 == 0 for v in rect), label
        if ratio is not None:
            rw, rh = ratio
            assert rect[2] * rh == rect[3] * rw, label
    assert crop_hit_test((20, 10, 160, 100), (20, 10)) == "nw"
    assert crop_hit_test((20, 10, 160, 100), (180, 110)) == "se"
    assert crop_hit_test((20, 10, 160, 100), (100, 60)) is None
    dragged = drag_crop_rect((20, 10), (185, 115), bounds, ratio=(16, 9), mode="se")
    assert all(v % 2 == 0 for v in dragged) and dragged[2] * 9 == dragged[3] * 16


def test_video_crop_widget_syncs_both_ways_and_handles_corners(qt_app, test_videos):
    with_audio, _ = test_videos
    editor = VideoEditor(with_audio)
    editor.resize(1120, 820)
    editor.show()
    qt_app.processEvents()
    cw = editor.crop_widget
    cw.resize(320, 180)
    qt_app.processEvents()

    # 1. Mit der Maus neuen Rahmen aufziehen -> Spinboxen und Checkbox synchronisieren sich
    p1 = cw.video_to_widget(20, 20).toPoint()
    p2 = cw.video_to_widget(220, 140).toPoint()
    QTest.mousePress(cw, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p1)
    QTest.mouseMove(cw, p2)
    QTest.mouseRelease(cw, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p2)
    qt_app.processEvents()
    assert editor.crop_enabled.isChecked()
    assert cw.crop_rect == tuple(w.value() for w in editor.crop)
    assert all(v % 2 == 0 for v in cw.crop_rect)
    assert cw.crop_rect[0] == pytest.approx(20, abs=4) and cw.crop_rect[2] == pytest.approx(200, abs=6)

    # 2. Eckgriff ('se') ziehen -> ändert untere rechte Ecke
    se_pos = cw.corner_handles()["se"].toPoint()
    se_target = cw.video_to_widget(280, 160).toPoint()
    QTest.mousePress(cw, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, se_pos)
    QTest.mouseMove(cw, se_target)
    QTest.mouseRelease(cw, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, se_target)
    qt_app.processEvents()
    assert cw.crop_rect == tuple(w.value() for w in editor.crop)
    assert (cw.crop_rect[0] + cw.crop_rect[2]) == pytest.approx(280, abs=6)

    # 3. Spinbox ändern (ungerade Zahl) -> rundet gerade und aktualisiert Widget
    editor.crop[0].setValue(31)
    editor.crop[2].setValue(161)
    assert editor.crop[0].value() == 30 and editor.crop[2].value() == 160
    assert cw.crop_rect[0] == 30 and cw.crop_rect[2] == 160

    # 4. Festes Seitenverhältnis wählen (16:9, 4:3, 1:1, 9:16)
    for label, ratio in CROP_RATIOS:
        if ratio is None:
            continue
        idx = editor.crop_ratio.findText(label)
        editor.crop_ratio.setCurrentIndex(idx)
        qt_app.processEvents()
        assert cw.crop_rect == tuple(w.value() for w in editor.crop)
        assert all(v % 2 == 0 for v in cw.crop_rect)
        assert cw.crop_rect[2] * ratio[1] == cw.crop_rect[3] * ratio[0]
    cw.grab()
    editor.close()


# --- C) Arbeitsbereich: Suche, Favoriten, Migration, Details-Dialog & Mehrfach-Tags -----------

def test_old_database_migration_adds_favorite_column(tmp_path):
    db_file = tmp_path / "workspace.sqlite"
    db = sqlite3.connect(db_file)
    db.execute("""CREATE TABLE captures (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, created TEXT NOT NULL,
        filename TEXT NOT NULL, width INTEGER, height INTEGER,
        tags TEXT DEFAULT '', collection TEXT DEFAULT 'Alle Aufnahmen',
        metadata TEXT NOT NULL)""")
    (tmp_path / "captures").mkdir(exist_ok=True)
    ident = "a" * 32
    Image.new("RGB", (20, 20), "red").save(tmp_path / "captures" / f"{ident}.png")
    db.execute("INSERT INTO captures VALUES (?, 'Alt', '2026-10-01T10:00:00+00:00', ?, 20, 20, '', 'Alle Aufnahmen', '{}')",
               (ident, f"{ident}.png"))
    db.commit()
    db.close()

    ws = Workspace(tmp_path)
    row = ws.get(ident)
    assert row["kind"] == "image" and row["favorite"] == 0
    assert ws.toggle_favorite(ident) is True
    assert ws.get(ident)["favorite"] == 1
    ws.remove(ident)
    ws.restore(ident)
    assert ws.get(ident)["favorite"] == 1
    ws.close()


def test_workspace_search_finds_metadata_and_companion_subtitles(tmp_path):
    ws = Workspace(tmp_path)
    img_win = ws.add(Image.new("RGB", (40, 30), "blue"), title="Eins",
                     metadata={"window_title": "Buchhaltung 2026", "application": "Tabellenprogramm", "url": "https://intranet.local/finanzen"})
    img_step = ws.add(Image.new("RGB", (40, 30), "green"), title="Zwei",
                      metadata={"window": "Terminal-Fenster", "app": "konsolenprogramm"})
    clip_file = tmp_path / "vortrag.mp4"
    clip_file.write_bytes(b"\0" * 512)
    (tmp_path / "vortrag.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nGeheimer Schlüsselbegriff im Untertitel\n", encoding="utf-8")
    vid_id = ws.add_file(clip_file, "video", title="Vortrag")
    txt_video = tmp_path / "notiz.mp4"
    txt_video.write_bytes(b"\0" * 512)
    vid2_id = ws.add_file(txt_video, "video", title="Zweitvideo")
    ws.path(vid2_id).with_suffix(".txt").write_text("Transkript mit Sondersuchwort", encoding="utf-8")

    assert [r["id"] for r in ws.rows("Buchhaltung")] == [img_win]
    assert [r["id"] for r in ws.rows("tabellenprogramm")] == [img_win]
    assert [r["id"] for r in ws.rows("intranet.local/finanzen")] == [img_win]
    assert [r["id"] for r in ws.rows("konsolenprogramm")] == [img_step]
    assert [r["id"] for r in ws.rows("Terminal-Fenster")] == [img_step]
    assert [r["id"] for r in ws.rows("Schlüsselbegriff")] == [vid_id]
    assert [r["id"] for r in ws.rows("Sondersuchwort")] == [vid2_id]
    ws.close()


def test_favorites_protected_from_retention_and_cleanup_and_shown_in_grid(qt_app, tmp_path, monkeypatch):
    from screenkaffee.app import MainWindow
    monkeypatch.setattr(MainWindow, "apply_hotkeys", lambda self: {})
    ws = Workspace(tmp_path)
    base_img = Image.new("RGB", (160, 120), "#225588")
    old_time = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    fav_img = ws.add(base_img, title="Wichtiges Bild")
    dup_img = ws.add(base_img, title="Kopie")
    old_img = ws.add(Image.new("RGB", (160, 120), "#882222"), title="Alt")
    vid_path = tmp_path / "big.mp4"
    vid_path.write_bytes(b"\0" * 2000)
    fav_vid = ws.add_file(vid_path, "video", title="Wichtiges Video")
    for ident in (fav_img, old_img, fav_vid):
        ws.db.execute("UPDATE captures SET created=? WHERE id=?", (old_time, ident))
    ws.db.commit()
    ws.set_favorite([fav_img, fav_vid], True)

    # 1. Aufbewahrungsregeln (cleanup_candidates) schützen Favoriten
    candidates = ws.cleanup_candidates(keep_latest=1, max_age_days=30)
    assert fav_img not in candidates and fav_vid not in candidates
    assert old_img in candidates

    # 2. cleanup.py (Duplikate & große/alte Videos) schützt Favoriten
    monkeypatch.setattr(cleanup, "OLD_VIDEO", 1000)
    report = cleanup.scan(ws)
    assert fav_img not in report.suggested()
    assert dup_img in report.suggested()
    assert all(e.ident != fav_vid for e, _ in report.videos)

    # 3. Sichtbarkeit im Raster (Stern) & Kontextmenü
    window = MainWindow(ws)
    fav_item = next(window.list.item(i) for i in range(window.list.count()) if window.list.item(i).data(Qt.ItemDataRole.UserRole) == fav_img)
    assert "★" in fav_item.text()
    window.select_id(old_img)
    menus = []
    class TestMenu(QMenu):
        def exec(self, *args):
            menus.append([act.text() for act in self.actions()])
    monkeypatch.setattr("screenkaffee.app.QMenu", TestMenu)
    window.show_capture_context_menu(QPoint(0, 0))
    assert any("★" in label and "Favorit" in label for label in menus[-1])
    window.toggle_favorite_selected()
    assert ws.get(old_img)["favorite"] == 1
    window.toggle_favorite_selected()
    assert ws.get(old_img)["favorite"] == 0
    ws.close()


def test_single_and_multi_selection_details_dialog_and_tags(qt_app, tmp_path, monkeypatch):
    from screenkaffee.app import MainWindow
    monkeypatch.setattr(MainWindow, "apply_hotkeys", lambda self: {})
    ws = Workspace(tmp_path)
    a = ws.add(Image.new("RGB", (40, 30), "red"), title="Alpha")
    b = ws.add(Image.new("RGB", (40, 30), "blue"), title="Beta")
    ws.update(a, "Alpha", "alt, gemeinsam", "Alle Aufnahmen")
    ws.update(b, "Beta", "gemeinsam, bleibt", "Alle Aufnahmen")

    # Einzelauswahl: EIN Dialog für Titel, Tags und Sammlung
    dlg1 = details_dialog(None, ws, [a])
    assert dlg1.title_edit.text() == "Alpha" and dlg1.tags_edit.text() == "alt, gemeinsam"
    dlg1.title_edit.setText("Alpha Neu")
    dlg1.tags_edit.setText("neu, fehler")
    dlg1.collection_combo.setEditText("Projekt X")
    dlg1.accept()
    row_a = ws.get(a)
    assert (row_a["title"], row_a["tags"], row_a["collection"]) == ("Alpha Neu", "neu, fehler", "Projekt X")

    # Mehrfachauswahl: Tags für alle hinzufügen und entfernen
    dlg2 = details_dialog(None, ws, [a, b])
    dlg2.add_tags_edit.setText("release, wichtig")
    dlg2.remove_tags_edit.setText("fehler, gemeinsam")
    dlg2.collection_combo.setEditText("Release")
    dlg2.accept()
    assert parse_tags(ws.get(a)["tags"]) == ["neu", "release", "wichtig"]
    assert parse_tags(ws.get(b)["tags"]) == ["bleibt", "release", "wichtig"]
    assert ws.get(a)["collection"] == "Release" and ws.get(b)["collection"] == "Release"

    # Aufruf über MainWindow.edit_details (F2)
    window = MainWindow(ws)
    window.select_id(a)
    monkeypatch.setattr(QDialog, "exec", lambda self: (
        self.title_edit.setText("Per F2"),
        self.tags_edit.setText("f2tag"),
        QDialog.DialogCode.Accepted,
    )[-1])
    window.edit_details()
    assert ws.get(a)["title"] == "Per F2" and ws.get(a)["tags"] == "f2tag"
    ws.close()


def test_cut_export_preserves_stream_sync_speed_crop_and_html(test_videos, tmp_path):
    from screenkaffee.media import export_html_page
    source, _ = test_videos
    target = tmp_path / "kombiniert.mp4"
    options = dict(start=0.25, end=2.75, cuts=[(1, 2)], speed=2,
                   crop=(10, 10, 160, 100))
    edit_video(source, target, **options)
    info = probe_media(target)
    assert (info.width, info.height) == (160, 100)
    assert ffprobe_duration(target) == pytest.approx(0.75, abs=0.15)
    result = run([tool("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(target)])
    streams = json.loads(result.stdout)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    audio = next(s for s in streams if s["codec_type"] == "audio")
    assert abs(float(video["start_time"]) - float(audio["start_time"])) < 0.06
    assert abs(float(video["duration"]) - float(audio["duration"])) < 0.1
    page = tmp_path / "vortrag.html"
    export_html_page(source, page, **options)
    html_video = next((tmp_path / "vortrag_dateien").glob("*.mp4"))
    assert page.is_file()
    assert ffprobe_duration(html_video) == pytest.approx(0.75, abs=0.15)


def test_crop_frame_follows_decoded_position(qt_app, test_videos):
    from PySide6.QtGui import QImage, QColor
    from PySide6.QtMultimedia import QVideoFrame
    editor = VideoEditor(test_videos[0])
    image = QImage(320, 180, QImage.Format.Format_RGBA8888)
    image.fill(QColor("#21ab54"))
    editor.video.videoSink().videoFrameChanged.emit(QVideoFrame(image))
    assert editor.crop_widget.pixmap.toImage().pixelColor(10, 10) == QColor("#21ab54")
    editor.close()


def test_details_cancel_and_bulk_tags_preserve_unselected_records(qt_app, tmp_path):
    ws = Workspace(tmp_path)
    a = ws.add(Image.new("RGB", (20, 20)), title="Titel")
    b = ws.add(Image.new("RGB", (20, 20)), title="Unver?ndert")
    ws.update(a, "Titel", "Alt, Bleibt", "Alle Aufnahmen")
    dialog = details_dialog(None, ws, [a])
    dialog.title_edit.setText("Verworfen")
    dialog.reject()
    assert ws.get(a)["title"] == "Titel"
    ws.update_tags([a], add="bleibt, Neu, neu", remove="ALT")
    assert ws.get(a)["tags"] == "Bleibt, Neu"
    assert ws.get(b)["tags"] == ""
    with pytest.raises(KeyError):
        ws.update_tags([a, "unbekannt"], add="Fehler")
    assert ws.get(a)["tags"] == "Bleibt, Neu"
    ws.close()


def test_cleanup_rechecks_favorites_before_removing(qt_app, tmp_path, monkeypatch):
    ws = Workspace(tmp_path)
    first = ws.add(Image.new("RGB", (30, 30), "red"))
    second = ws.add(Image.new("RGB", (30, 30), "red"))
    ws.set_favorite(first)
    report = cleanup.scan(ws)
    dialog = cleanup.cleanup_dialog(None, ws, report)
    assert dialog.checked() == [second]
    ws.set_favorite(second)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    dialog.remove_checked()
    assert ws.get(first) is not None and ws.get(second) is not None
    ws.close()


def test_video_sidecars_survive_trash_and_restore(tmp_path):
    ws = Workspace(tmp_path / "ablage")
    source = tmp_path / "film.mp4"
    source.write_bytes(b"videodaten")
    source.with_suffix(".srt").write_text("Suchbegriff", encoding="utf-8")
    source.with_suffix(".txt").write_text("Transkript", encoding="utf-8")
    ident = ws.add_file(source)
    path = ws.path(ident)
    ws.remove(ident)
    assert not path.with_suffix(".srt").exists()
    ws.restore(ident)
    assert path.with_suffix(".srt").read_text(encoding="utf-8") == "Suchbegriff"
    assert path.with_suffix(".txt").read_text(encoding="utf-8") == "Transkript"
    assert [row["id"] for row in ws.rows("Suchbegriff")] == [ident]
    ws.close()
