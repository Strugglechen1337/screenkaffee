"""Aufnahmen vergleichen, aufs Handy senden (QR/WLAN) und Zeitraffer."""
import time
import urllib.error
import urllib.request

import numpy as np
import pytest
from PIL import Image

from screenkaffee import compare, lanshare, timelapse


def window_for(tmp_path, monkeypatch):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    monkeypatch.setattr(MainWindow, "apply_hotkeys", lambda self: {})
    window = MainWindow(Workspace(tmp_path))
    monkeypatch.setattr(window, "task", lambda operation, done, message: done(operation()))
    return window


def with_squares(*boxes, size=(200, 120), color="red"):
    image = Image.new("RGB", size, "white")
    for box in boxes:
        image.paste(color, box)
    return image


# --- Vergleichen ---------------------------------------------------------------------------------------------------

def test_identical_images_have_no_difference():
    image = with_squares((10, 10, 40, 40))
    diff = compare.difference(image, image.copy())
    assert diff.regions == [] and diff.ratio == 0 and diff.summary() == "Keine Unterschiede gefunden"


def test_separate_changes_become_separate_regions():
    before = with_squares()
    after = with_squares((10, 10, 30, 25), (150, 80, 190, 110))
    diff = compare.difference(before, after)
    assert diff.regions == [(150, 80, 190, 110), (10, 10, 30, 25)]          # größter zuerst, pixelgenau
    assert diff.ratio == pytest.approx((40 * 30 + 20 * 15) / (200 * 120))
    assert diff.summary().startswith("2 Bereiche verändert · ") and "," in diff.summary()


def test_tolerance_ignores_compression_noise():
    before = with_squares()
    noisy = Image.fromarray((np.asarray(before, dtype=np.int16) - 10).clip(0, 255).astype(np.uint8))
    assert compare.difference(before, noisy).regions == []
    assert compare.difference(before, noisy, threshold=5).ratio == 1.0


def test_different_sizes_count_the_extra_area():
    diff = compare.difference(Image.new("RGB", (100, 50), "white"), Image.new("RGB", (100, 80), "white"))
    assert diff.size == (100, 80) and diff.regions == [(0, 50, 100, 80)]


def test_highlight_marks_changes_red_and_fades_the_rest():
    before, after = with_squares(), with_squares((60, 40, 100, 80), color="blue")
    marked = compare.highlight(before, after)
    assert marked.size == (200, 120)
    r, g, b = marked.getpixel((80, 60))
    assert r > 100 and g < 60                                                # rot getöntes Blau
    assert marked.getpixel((5, 5)) == (255, 255, 255)                        # Unverändertes bleibt hell
    assert marked.getpixel((57, 37))[0] > 200 and marked.getpixel((57, 37))[1] < 80     # Rahmen um den Bereich


def test_compare_window_modes_swap_and_save(qt_app):
    saved = []
    window = compare.CompareWindow(with_squares(), with_squares((10, 10, 50, 50)), ("Alt", "Neu"), on_save=saved.append)
    assert window.windowTitle() == "Vergleichen: Alt ↔ Neu" and window.summary.text().startswith("1 Bereich verändert")
    window.mode.setCurrentIndex(window.mode.findData("diff"))
    assert window.canvas.mode == "diff" and window.slider.isHidden()
    window.mode.setCurrentIndex(window.mode.findData("slider"))
    window.slider.setValue(20)
    assert window.canvas.position == 20
    window.resize(800, 600)
    window.canvas.grab()                                                     # alle Ansichten zeichnen ohne Fehler
    window.swap()
    assert window.titles == ["Neu", "Alt"]
    window.save_button.click()
    assert saved and saved[0].size == (200, 120) and window.save_button.text() == "Gespeichert ✓"
    window.threshold.setValue(128)
    assert window.summary.text().startswith("1 Bereich")                     # Rot auf Weiß bleibt trotz hoher Toleranz
    window.close()


def test_compare_from_workspace_puts_the_older_capture_left(qt_app, tmp_path, monkeypatch):
    window = window_for(tmp_path, monkeypatch)
    first = window.workspace.add(with_squares(), title="Vorher")
    second = window.workspace.add(with_squares((0, 0, 20, 20)), title="Nachher")
    window.workspace.db.execute("UPDATE captures SET created='2026-01-01T00:00:00+01:00' WHERE id=?", (first,))
    window.workspace.db.commit()
    window.refresh()
    window.list.selectAll()
    view = window.compare_selected()
    assert view.titles == ["Vorher", "Nachher"]
    view.save_button.click()
    rows = window.workspace.rows()
    assert any(row["title"] == "Vergleich Vorher ↔ Nachher" for row in rows)
    view.close()
    window.select_id(second)
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *a, **k: None)
    assert window.compare_selected() is None                                 # nur eine Aufnahme gewählt


# --- Aufs Handy senden ---------------------------------------------------------------------------------------------

def test_lan_addresses_are_private_ipv4_only():
    assert lanshare.is_lan("192.168.1.20") and lanshare.is_lan("10.0.0.5") and lanshare.is_lan("172.20.1.1")
    assert not lanshare.is_lan("127.0.0.1") and not lanshare.is_lan("8.8.8.8") and not lanshare.is_lan("169.254.3.4")
    assert not lanshare.is_lan("fe80::1") and not lanshare.is_lan("kein-ip")
    assert all(lanshare.is_lan(address) for address in lanshare.local_addresses())


def test_qr_code_decodes_to_the_link():
    import zxingcpp
    url = "http://192.168.178.24:50123/AbCdEf/"
    image = lanshare.qr_image(url, 300)
    assert image.width >= 250
    assert zxingcpp.read_barcode(image.convert("RGB")).text == url


def fetch(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.headers, response.read()


def test_share_serves_only_the_shared_files_under_the_secret_path(tmp_path):
    picture = tmp_path / "bild.png"
    Image.new("RGB", (4, 4), "red").save(picture)
    movie = tmp_path / "clip.mp4"
    movie.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 100)
    share = lanshare.Share([(picture, "Fehler <im> Menü"), (movie, "")], address="127.0.0.1")
    try:
        assert share.url.startswith("http://127.0.0.1:") and len(share.token) >= 16
        status, headers, body = fetch(share.url)
        assert status == 200 and "text/html" in headers["Content-Type"]
        page = body.decode("utf-8")
        assert "Fehler &lt;im&gt; Menü" in page and "<video" in page and "Herunterladen" in page
        assert share.visits == 1 and share.downloads == 0
        status, headers, body = fetch(share.url + "file/0")
        assert body == picture.read_bytes() and headers["Content-Type"] == "image/png"
        assert headers["Content-Disposition"].startswith("inline")
        assert share.downloads == 0
        status, headers, body = fetch(share.url + "file/1?download=1")
        assert body == movie.read_bytes() and headers["Content-Disposition"].startswith("attachment")
        assert "clip.mp4" in headers["Content-Disposition"]
        time.sleep(0.1)
        assert share.downloads == 1 and lanshare.status_text(share) == "✓ 1× heruntergeladen"
        base = share.url.rsplit("/", 2)[0]
        for wrong in (base + "/", base + "/falsch/", share.url + "file/2", share.url + "file/../bild.png",
                      share.url.replace(share.token, share.token[:-1] + "x")):
            with pytest.raises(urllib.error.HTTPError) as error:
                fetch(wrong)
            assert error.value.code == 404
    finally:
        share.stop()
    share.stop()                                                             # zweimal stoppen ist harmlos
    with pytest.raises(urllib.error.URLError):
        fetch(share.url)


def test_share_expires(tmp_path):
    picture = tmp_path / "bild.png"
    Image.new("RGB", (4, 4)).save(picture)
    share = lanshare.Share([(picture, "x")], address="127.0.0.1", timeout=0.2)
    time.sleep(0.6)
    assert share.expired and share.stopped and "abgelaufen" in lanshare.status_text(share)


def test_share_needs_files_and_a_network(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        lanshare.Share([(tmp_path / "fehlt.png", "x")], address="127.0.0.1")
    picture = tmp_path / "bild.png"
    Image.new("RGB", (4, 4)).save(picture)
    monkeypatch.setattr(lanshare, "local_addresses", lambda: [])
    with pytest.raises(RuntimeError, match="selben WLAN"):
        lanshare.Share([(picture, "x")])


def test_phone_share_from_workspace_prepares_and_cleans_up(qt_app, tmp_path, monkeypatch):
    monkeypatch.setattr(lanshare, "local_addresses", lambda: ["127.0.0.1"])
    window = window_for(tmp_path / "daten", monkeypatch)
    ident = window.workspace.add(with_squares((0, 0, 10, 10)), title="Rechnung: März")
    window.refresh()
    window.select_id(ident)
    dialog = window.share_to_phone()
    share = dialog.share
    assert dialog.windowTitle() == "Aufs Handy senden" and not dialog.qr.pixmap().isNull()
    assert dialog.link.text() == share.url and dialog.status.text() == "Warte auf das Handy …"
    assert dialog.address.isHidden()                                         # nur eine Adresse → keine Auswahl
    staged = share.files[0][0]
    assert staged.is_file() and staged.name == "1-Rechnung_ März.png"
    assert fetch(share.url + "file/0")[2] == staged.read_bytes()
    dialog.close()
    assert share.stopped and not staged.parent.exists()


def test_phone_share_respects_the_policy(qt_app, tmp_path, monkeypatch):
    window = window_for(tmp_path, monkeypatch)
    window.policy["disabled"].append("lan_share")
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *a, **k: None)
    assert window.share_to_phone() is None


# --- Zeitraffer ----------------------------------------------------------------------------------------------------

def test_describe_the_speedup():
    assert timelapse.describe(5, 30) == "1 Stunde Aufnahme → 24 s Video (150× schneller)"
    assert timelapse.describe(1, 10) == "1 Stunde Aufnahme → 6,0 min Video (10× schneller)"


def wait_until(condition, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not condition():
        time.sleep(0.02)
    return condition()


def test_timelapse_records_frames_with_a_fixed_size(tmp_path):
    sizes = iter([(101, 61), (300, 200)])
    def grab():
        return Image.new("RGB", next(sizes, (101, 61)), "navy")
    lapse = timelapse.Timelapse(tmp_path, grab, interval=0.05, fps=10)
    lapse.start()
    assert wait_until(lambda: lapse.frames >= 4)
    lapse.stop()
    assert not lapse.active and lapse.size == (100, 60)
    frames = sorted(lapse.folder.glob("frame_*.jpg"))
    assert len(frames) == lapse.frames and {Image.open(f).size for f in frames} == {(100, 60)}
    lapse.discard()
    assert not lapse.folder.exists()


def test_timelapse_stops_by_itself(tmp_path):
    lapse = timelapse.Timelapse(tmp_path, lambda: Image.new("RGB", (20, 20)), interval=0.05, max_seconds=0.15)
    lapse.start()
    assert wait_until(lambda: not lapse.active)
    assert lapse.finished_reason == "Höchstdauer erreicht" and lapse.frames >= 3
    def broken():
        raise RuntimeError("Monitor weg")
    failing = timelapse.Timelapse(tmp_path, broken, interval=0.01)
    failing.start()
    assert wait_until(lambda: not failing.active)
    assert failing.frames == 0 and "Monitor weg" in failing.finished_reason
    with pytest.raises(RuntimeError, match="Zu wenige Bilder"):
        failing.render()
    assert not failing.folder.exists()


def test_timelapse_becomes_an_mp4(tmp_path):
    from screenkaffee.media import probe_media
    colors = iter(["red", "green", "blue", "white"] * 10)
    lapse = timelapse.Timelapse(tmp_path, lambda: Image.new("RGB", (64, 48), next(colors)), interval=0.02, fps=10)
    lapse.start()
    assert wait_until(lambda: lapse.frames >= 5)
    path = lapse.render()
    info = probe_media(path)
    assert path.suffix == ".mp4" and (info.width, info.height) == (64, 48)
    assert not lapse.folder.exists()


def test_timelapse_from_the_app(qt_app, tmp_path, monkeypatch):
    window = window_for(tmp_path / "daten", monkeypatch)
    dialog = window.configure_timelapse()
    assert dialog.windowTitle() == "Zeitraffer" and "150× schneller" in dialog.preview.text()
    dialog.interval.setValue(2)
    assert "60× schneller" in dialog.preview.text()
    started = []
    monkeypatch.setattr(window, "begin_timelapse", lambda region, grab=None: started.append(region))
    dialog.accept()
    assert window.settings["timelapse_interval"] == 2 and window.pending_timelapse["interval"] == 2
    assert started and len(started[0]) == 4                                 # Gewählter Monitor
    monkeypatch.undo()
    window = window_for(tmp_path / "daten2", monkeypatch)
    window.pending_timelapse = {"interval": 0.02, "fps": 10}
    lapse = window.begin_timelapse([0, 0, 32, 32], grab=lambda: Image.new("RGB", (32, 32), "orange"))
    assert window.timelapse is lapse and window.timelapse_action.text() == "Zeitraffer stoppen"
    assert wait_until(lambda: lapse.frames >= 3)
    window.check_timelapse()
    assert window.statusBar().currentMessage().startswith("Zeitraffer läuft · ")
    imported = []
    monkeypatch.setattr(window, "import_video_path", imported.append)
    window.command_action("timelapse")                                       # Tastenkürzel stoppt
    assert window.timelapse is None and window.timelapse_action.text() == "Zeitraffer starten …"
    assert imported and imported[0].endswith(".mp4")
