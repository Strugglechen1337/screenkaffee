"""Schicker Screenshot, Tastendrücke im Video und die nachladbare Untertitel-Erweiterung."""
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from screenkaffee import addons, beautify, updater
from screenkaffee.videoextras import KeyTracker, KeystrokeOverlay, key_name

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import package_addons  # noqa: E402


def window_for(tmp_path, monkeypatch):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    monkeypatch.setattr(MainWindow, "apply_hotkeys", lambda self: {})
    window = MainWindow(Workspace(tmp_path))
    monkeypatch.setattr(window, "task", lambda operation, done, message: done(operation()))
    return window


# --- Schicker Screenshot -------------------------------------------------------------------------------------------

def test_beautify_adds_background_shadow_and_round_corners():
    shot = Image.new("RGB", (400, 200), "white")
    result = beautify.beautify(shot, style="ocean", padding=50, radius=20)
    assert result.size == (500, 300) and result.mode == "RGB"
    assert result.getpixel((250, 150)) == (255, 255, 255)                    # Bild bleibt unverändert in der Mitte
    assert result.getpixel((51, 51)) != (255, 255, 255)                                         # abgerundete Ecke zeigt den Hintergrund
    assert result.getpixel((5, 5)) != result.getpixel((494, 294))            # Farbverlauf
    below = result.getpixel((250, 262))
    plain = beautify.beautify(shot, style="ocean", padding=50, radius=20, shadow=False).getpixel((250, 262))
    assert sum(below) < sum(plain)                                           # Schatten unter dem Bild


def test_beautify_frames_aspect_and_auto_padding():
    shot = Image.new("RGB", (1000, 500), "white")
    framed = beautify.beautify(shot, frame="mac", title="Rechnung", padding=0, radius=0, shadow=False)
    bar = max(28, round(1000 * 0.028))
    assert framed.size == (1000, 500 + bar)
    assert framed.getpixel((round(bar * 0.62), bar // 2)) == (255, 95, 87)  # rote Ampel
    windows = beautify.beautify(shot, frame="windows", padding=0, radius=0, shadow=False)
    assert windows.getpixel((500, 2)) == (255, 255, 255)
    wide = beautify.beautify(shot, aspect="16:9")
    assert abs(wide.width / wide.height - 16 / 9) < 0.01
    square = beautify.beautify(shot, aspect="1:1")
    assert square.width == square.height
    auto = beautify.beautify(shot)                                           # automatisch ≈ 7 % Abstand
    assert auto.size == (1000 + 2 * 70, 500 + 2 * 70)
    options = beautify.Options(frame="mac-dark", padding=-1, aspect="9:16")
    assert beautify.beautify(shot, options).size == beautify.beautify_size(shot, options)


def test_options_ignore_unknown_values():
    options = beautify.Options.from_dict({"style": "gibtsnicht", "frame": "x", "aspect": "7:3", "radius": 3, "fremd": 1})
    assert (options.style, options.frame, options.aspect, options.radius) == ("coffee", "none", "free", 3)


def test_beautify_dialog_previews_and_saves(qt_app, tmp_path):
    saved, settings = [], {"beautify": {"style": "mint", "radius": 6}}
    dialog = beautify.beautify_dialog(None, Image.new("RGB", (1800, 900), "white"), settings, on_save=saved.append, title="Fehler")
    assert dialog.style.currentData() == "mint" and dialog.title.text() == "Fehler"
    assert not dialog.preview.pixmap().isNull() and dialog.size_label.text() == "Ergebnis: 2052 × 1152 px"
    dialog.frame.setCurrentIndex(dialog.frame.findData("mac"))
    dialog.refresh()
    assert settings["beautify"]["frame"] == "mac"
    dialog.save_button.click()
    assert saved and saved[0].size == beautify.beautify_size(Image.new("RGB", (1800, 900)), dialog.current())
    dialog.copy_button.click()
    assert qt_app.clipboard().image().width() == saved[0].width
    dialog.close()


def test_beautify_from_workspace_and_editor(qt_app, tmp_path, monkeypatch):
    window = window_for(tmp_path, monkeypatch)
    ident = window.workspace.add(Image.new("RGB", (300, 200), "red"), title="Login")
    window.refresh()
    window.select_id(ident)
    dialog = window.beautify_selected()
    assert dialog.title.text() == "Login"
    dialog.save_button.click()
    assert any(row["title"] == "Login · schick" for row in window.workspace.rows())
    dialog.close()
    assert "beautify" in json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    from screenkaffee.editor import EditorWindow
    editor = EditorWindow(Image.new("RGB", (80, 60), "blue"), "Bug")
    results = []
    editor.saved.connect(results.append)
    view = editor.beautify_current()
    view.save_button.click()
    assert results and results[0].width > 80
    view.close()
    editor.close()


# --- Tastendrücke ----------------------------------------------------------------------------------------------------

CTRL, SHIFT, ALT, WIN = 0xA2, 0xA0, 0xA4, 0x5B


def test_combos_mode_shows_shortcuts_but_hides_typing():
    tracker = KeyTracker("combos", char_for=lambda vk: "")
    assert tracker.feed({0x48}, 0) is None                                   # H getippt
    assert tracker.feed({SHIFT, 0x48}, 0.1) is None                          # Umschalt+H ist auch Tippen
    assert tracker.feed(set(), 0.2) is None
    assert tracker.feed({CTRL}, 0.3) is None
    assert tracker.feed({CTRL, 0x43}, 0.4) == "Strg + C"
    assert tracker.feed({CTRL, 0x43}, 0.5) is None                           # gehalten: nicht wiederholen
    assert tracker.feed({CTRL, SHIFT, 0x53}, 0.6) == "Strg + Umschalt + S"
    assert tracker.feed({ALT, 0x73}, 0.7) == "Alt + F4"
    assert tracker.feed({WIN, 0x44}, 0.8) == "Win + D"
    assert tracker.feed({0x0D}, 0.9) == "Eingabe"
    assert tracker.feed({0x1B}, 1.0) == "Esc"
    assert tracker.feed({0x09}, 1.1) is None                                 # Tab im Formular bleibt unsichtbar
    assert tracker.feed({0x20}, 1.2) is None and tracker.feed({0x08}, 1.3) is None
    assert tracker.feed({0x01, 0x02}, 1.4) is None                           # Maustasten nie


def test_all_mode_collects_typed_text():
    tracker = KeyTracker("all", char_for=lambda vk: "#" if vk == 0xBF else "")
    assert tracker.feed({0x48}, 0) == "h"
    tracker.feed(set(), 0.05)
    assert tracker.feed({SHIFT, 0x41}, 0.1) == "hA"
    tracker.feed(set(), 0.15)
    assert tracker.feed({0x20}, 0.2) == "hA "
    tracker.feed(set(), 0.25)
    assert tracker.feed({0xBF}, 0.3) == "hA #"
    tracker.feed(set(), 0.35)
    assert tracker.feed({0x08}, 0.4) == "hA "
    tracker.feed(set(), 0.45)
    assert tracker.feed({0x58}, 3.0) == "x"                                  # nach einer Pause beginnt ein neues Feld
    tracker.feed(set(), 3.1)
    assert tracker.feed({CTRL, 0x5A}, 3.2) == "Strg + Z"


def test_key_names():
    assert [key_name(vk, lambda v: "") for vk in (0x41, 0x37, 0x70, 0x87, 0x62, 0x25, 0x2E)] == ["A", "7", "F1", "F24", "Num 2", "←", "Entf"]
    assert key_name(0xBA, lambda vk: "Ü") == "Ü" and key_name(0xE5, lambda vk: "") == "Taste 229"


def test_keystroke_overlay_shows_and_fades(qt_app):
    from PySide6.QtCore import QRect
    now, keys = [0.0], [set()]
    overlay = KeystrokeOverlay(QRect(0, 0, 1280, 720), pressed=lambda: keys[0], clock=lambda: now[0], char_for=lambda vk: "")
    keys[0] = {CTRL, 0x56}
    overlay.tick()
    assert overlay.text == "Strg + V" and overlay.isVisible() and overlay.opacity() == 1.0
    geometry = overlay.geometry()
    assert abs(geometry.center().x() - 640) <= 1 and geometry.bottom() < 720 and overlay.windowFlags() & overlay.windowFlags().WindowTransparentForInput
    keys[0] = set()
    now[0] = overlay.SHOW + overlay.FADE / 2
    overlay.tick()
    assert 0 < overlay.opacity() < 1
    overlay.grab()
    now[0] = overlay.SHOW + overlay.FADE + 0.1
    overlay.tick()
    assert not overlay.isVisible()
    overlay.stop()


def test_keystroke_overlay_follows_the_video_region(qt_app, tmp_path, monkeypatch):
    window = window_for(tmp_path, monkeypatch)
    overlay = window.start_keystroke_overlay([100, 50, 640, 360], "combos")
    scale = window.screen_for_region([100, 50, 640, 360]).devicePixelRatio() if sys.platform == "win32" else 1
    assert overlay.area.width() == round(640 / scale) and window.settings["video_keystrokes"] == "combos"
    window.end_recording_extras()
    assert window.keystroke_overlay is None


# --- Untertitel-Erweiterung -----------------------------------------------------------------------------------------

def fake_app(root):
    """Ein x64-Paket ohne Untertitel-Teile plus die passende Erweiterung, wie package_windows.py sie baut."""
    (root / "_internal").mkdir(parents=True)
    (root / "screenkaffee.exe").write_text("exe")
    (root / "_internal" / "python312.dll").write_text("py")
    source = root.parent / "quelle"
    (source / "_internal" / "ctranslate2").mkdir(parents=True)
    (source / "_internal" / "ctranslate2" / "ctranslate2.dll").write_bytes(b"dll" * 100)
    (source / "_internal" / "av.libs").mkdir(parents=True)
    (source / "_internal" / "av.libs" / "avcodec.dll").write_bytes(b"codec")
    (source / "models" / "whisper-base").mkdir(parents=True)
    (source / "models" / "whisper-base" / "model.bin").write_bytes(b"gewichte")
    entries = [(p.relative_to(source).as_posix(), p) for p in source.rglob("*") if p.is_file()]
    archive = root.parent / package_addons.SPEECH_ASSETS["windows-x64"]
    info = package_addons.write_addon(entries, archive)
    (root / "addons.json").write_text(json.dumps(info), encoding="utf-8")
    return archive, info


class FakeGitHub:
    def __init__(self, archive, tag):
        self.data = archive.read_bytes()
        self.tag, self.requests = tag, []

    def __call__(self, request, timeout=30):
        url = request.full_url
        self.requests.append(url)
        if url.endswith("/releases/tags/" + self.tag):
            body = {"tag_name": self.tag, "assets": [{"name": package_addons.SPEECH_ASSETS["windows-x64"], "url": "https://api/asset/1",
                    "size": len(self.data), "digest": "sha256:" + hashlib.sha256(self.data).hexdigest()}]}
            return io.BytesIO(json.dumps(body).encode())
        if url == "https://api/asset/1":
            return io.BytesIO(self.data)
        raise AssertionError(url)


def test_addon_archive_layout_and_fingerprint(tmp_path):
    root = tmp_path / "app"
    archive, info = fake_app(root)
    assert info["speech"]["files"] == 3 and info["speech"]["bytes"] == 300 + 5 + 8
    with zipfile.ZipFile(archive) as source:
        meta = json.loads(source.read("addon.json"))
        assert meta["fingerprint"] == info["speech"]["fingerprint"]
        assert sorted(meta["files"]) == ["_internal/av.libs/avcodec.dll", "_internal/ctranslate2/ctranslate2.dll", "models/whisper-base/model.bin"]
        assert source.read("screenkaffee/models/whisper-base/model.bin") == b"gewichte"
    assert package_addons.is_speech_site_path("av.libs/x.dll") and package_addons.is_speech_site_path("faster_whisper/audio.py")
    assert not package_addons.is_speech_site_path("avahi/x") and not package_addons.is_speech_site_path("PySide6/QtCore.pyd")
    with pytest.raises(RuntimeError, match="Whisper-Modell fehlt"):
        package_addons.write_addon([("x/y.dll", archive)], tmp_path / "leer.zip")


def test_speech_addon_install_relink_after_update_and_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    from screenkaffee import __version__
    root, data = tmp_path / "app", tmp_path / "app" / "data"
    archive, info = fake_app(root)
    assert addons.speech_status(root, data) == "missing" and not addons.ensure_speech(root, data)
    github = FakeGitHub(archive, "v" + __version__)
    seen = []
    addons.install_speech(data, root, opener=github, progress=lambda done, total: seen.append((done, total)))
    assert github.requests[0].endswith("/releases/tags/v" + __version__) and seen[-1] == (len(github.data),) * 2
    assert (root / "_internal" / "ctranslate2" / "ctranslate2.dll").read_bytes() == b"dll" * 100
    assert addons.speech_status(root, data) == "installed" and addons.ensure_speech(root, data)
    assert not (data / "addons" / "download").exists() and not (data / "addons" / "speech-unpacking").exists()
    # Nach einem Update sind die Programmdateien neu – die Erweiterung wird ohne Download wieder verlinkt.
    import shutil
    shutil.rmtree(root / "_internal")
    shutil.rmtree(root / "models")
    (root / addons.MARKER).unlink()
    (root / "_internal").mkdir()
    (root / "_internal" / "python312.dll").write_text("neu")
    assert addons.speech_status(root, data) == "cached"
    assert addons.ensure_speech(root, data) and (root / "models" / "whisper-base" / "model.bin").is_file()
    freed = addons.remove_speech(data, root)
    assert freed >= 313 and addons.speech_status(root, data) == "missing"
    assert not (root / "models").exists() and (root / "_internal").is_dir()


def test_speech_addon_rejects_wrong_fingerprint_and_arm64(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    from screenkaffee import __version__
    root, data = tmp_path / "app", tmp_path / "data"
    archive, info = fake_app(root)
    info["speech"]["fingerprint"] = "0" * 64
    (root / "addons.json").write_text(json.dumps(info), encoding="utf-8")
    with pytest.raises(addons.AddonError, match="passt nicht"):
        addons.install_speech(data, root, opener=FakeGitHub(archive, "v" + __version__))
    assert addons.speech_status(root, data) == "missing" and not list((data / "addons").glob("speech-*"))
    (root / "addons.json").write_text("{}", encoding="utf-8")
    assert addons.speech_status(root, data) == "unsupported"
    with pytest.raises(addons.AddonError, match="ARM64"):
        addons.install_speech(data, root)
    assert addons.speech_status(tmp_path / "quellcode", data) == "builtin"


def test_video_editor_asks_before_downloading(qt_app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    window = window_for(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(addons, "ensure_speech", lambda root, data: False)
    monkeypatch.setattr(addons, "speech_status", lambda root, data: "missing")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    assert window.prepare_speech(window, lambda: calls.append("weiter")) is False and calls == []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(addons, "install_speech", lambda *a, **k: {"fingerprint": "x"})
    assert window.prepare_speech(window, lambda: calls.append("weiter")) is False
    assert calls == ["weiter"] and window.statusBar().currentMessage() == "Untertitel-Erweiterung installiert"
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    def broken(*a, **k):
        raise updater.UpdateError("GitHub ist nicht erreichbar")
    monkeypatch.setattr(addons, "install_speech", broken)
    window.prepare_speech(window, lambda: calls.append("nochmal"))
    assert calls == ["weiter"] and "nicht erreichbar" in warnings[0]
    monkeypatch.setattr(addons, "ensure_speech", lambda root, data: True)
    assert window.prepare_speech(window, lambda: None) is True
    assert "Quellcode" in window.speech_status_text() or "Untertitel" in window.speech_status_text()


def test_updater_release_by_tag():
    class Opener:
        def __call__(self, request, timeout=30):
            assert request.full_url.endswith("/releases/tags/v0.9.0")
            return io.BytesIO(json.dumps({"tag_name": "v0.9.0", "assets": []}).encode())
    assert updater.release_by_tag("v0.9.0", opener=Opener()).version == (0, 9, 0)


def test_speech_addon_survives_paths_longer_than_260_characters(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    from screenkaffee import __version__
    root = tmp_path / "app"
    archive, info = fake_app(root)
    data = tmp_path / ("tief" * 8) / ("ordner" * 4) / "data"         # Datenordner weit unten wie in OneDrive
    deep = "_internal/" + "u" * 120 + "/" + "x" * 90 + ".dll"
    source = tmp_path / "lang"
    (source / "models" / "whisper-base").mkdir(parents=True)
    (source / "models" / "whisper-base" / "model.bin").write_bytes(b"m")
    entries = [("models/whisper-base/model.bin", source / "models" / "whisper-base" / "model.bin"), (deep, archive)]
    info = package_addons.write_addon(entries, archive.with_name("lang.zip"))
    info["speech"]["asset"] = package_addons.SPEECH_ASSETS["windows-x64"]
    archive.with_name("lang.zip").replace(archive)
    (root / "addons.json").write_text(json.dumps(info), encoding="utf-8")
    assert len(str(addons.cache_dir(data, info["speech"]["fingerprint"]) / "screenkaffee" / deep)) > 260
    addons.install_speech(data, root, opener=FakeGitHub(archive, "v" + __version__))
    assert addons.speech_status(root, data) == "installed"
    assert addons.remove_speech(data, root) > 0 and addons.speech_status(root, data) == "missing"
