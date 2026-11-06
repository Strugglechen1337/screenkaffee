"""Selbst-Update aus GitHub-Releases (ohne Netz: GitHub wird durch einen Fake-Opener ersetzt)."""
import hashlib
import io
import json
import sys
import tarfile
import zipfile

import pytest

from screenkaffee import updater


def make_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def fake_github(payloads, seen=None):
    """payloads: URL -> bytes. Merkt sich, ob eine Anfrage Anmeldedaten trug (darf nie passieren)."""
    def opener(request, timeout=None):
        if seen is not None:
            seen.append((request.full_url, request.unredirected_hdrs.get("Authorization")))
        if request.full_url not in payloads:
            raise updater.urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        return io.BytesIO(payloads[request.full_url])
    return opener


def release_payload(package_bytes, name="screenkaffee-Windows-x64.zip", digest=None, verification=True):
    api = "https://api.github.com/repos/o/r"
    assets = [{"name": name, "url": f"{api}/releases/assets/1", "size": len(package_bytes), "digest": digest}]
    payloads = {f"{api}/releases/assets/1": package_bytes}
    if verification:
        assets.append({"name": "verification.json", "url": f"{api}/releases/assets/2", "size": 10})
        payloads[f"{api}/releases/assets/2"] = json.dumps(
            {"artifacts": [{"name": name, "sha256": hashlib.sha256(package_bytes).hexdigest()}]}).encode()
    payloads[f"{api}/releases/latest"] = json.dumps({"tag_name": "v0.7.0", "body": "## Neu\n- Updater",
        "html_url": "https://github.com/o/r/releases/tag/v0.7.0", "assets": assets}).encode()
    return payloads


def test_versions_compare_numerically():
    assert updater.parse_version("v0.7.0") == (0, 7, 0)
    assert updater.parse_version("0.10.1") > updater.parse_version("v0.9.9")
    assert updater.parse_version("v1.0.0-beta") == (1, 0, 0)
    assert updater.current_version() == updater.parse_version(__import__("screenkaffee").__version__)


def test_installed_variant_detects_all_three_packages(tmp_path, monkeypatch):
    x64, arm, linux = tmp_path / "x64", tmp_path / "arm", tmp_path / "linux"
    (x64 / "_internal").mkdir(parents=True)
    (x64 / "screenkaffee.exe").write_bytes(b"MZ")
    (arm / "runtime").mkdir(parents=True)
    (arm / "runtime" / "pythonw.exe").write_bytes(b"MZ")
    (arm / "screenkaffee.exe").write_bytes(b"MZ")
    (linux / "runtime" / "bin").mkdir(parents=True)
    (linux / "screenkaffee").write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "platform", "win32")
    assert updater.installed_variant(x64) == "windows-x64"
    assert updater.installed_variant(arm) == "windows-arm64"
    assert updater.installed_variant(tmp_path) is None              # Quellcode-Version
    monkeypatch.setattr(sys, "platform", "linux")
    assert updater.installed_variant(linux) == "linux-x86_64"
    assert updater.installed_variant(x64) is None


def test_release_lookup_without_token():
    seen = []
    payloads = release_payload(b"zip")
    release = updater.latest_release("o/r", fake_github(payloads, seen))
    assert release.tag == "v0.7.0" and release.version == (0, 7, 0) and "Updater" in release.notes
    assert seen[0][1] is None                  # öffentliches Repository: nie Anmeldedaten senden
    with pytest.raises(updater.UpdateError, match="Kein passendes Release"):
        updater.latest_release("o/fehlt", fake_github({}))


def test_download_checks_sha256(tmp_path):
    data = make_zip({"screenkaffee/screenkaffee.exe": b"neu"})
    payloads = release_payload(data)
    opener = fake_github(payloads)
    release = updater.latest_release("o/r", opener)
    target = updater.download(release, "screenkaffee-Windows-x64.zip", tmp_path / "a.zip", opener=opener)
    assert target.read_bytes() == data
    payloads["https://api.github.com/repos/o/r/releases/assets/1"] = b"manipuliert"
    with pytest.raises(updater.UpdateError, match="Prüfsumme"):
        updater.download(release, "screenkaffee-Windows-x64.zip", tmp_path / "b.zip", opener=opener)
    assert not (tmp_path / "b.zip").exists()
    # GitHub-Digest wird bevorzugt; ohne Digest und ohne verification.json kein Update
    digest_release = updater.latest_release("o/r", fake_github(
        release_payload(data, digest="sha256:" + hashlib.sha256(data).hexdigest(), verification=False)))
    assert updater.expected_sha256(digest_release, "screenkaffee-Windows-x64.zip") == hashlib.sha256(data).hexdigest()
    bare = updater.latest_release("o/r", fake_github(release_payload(data, verification=False)))
    with pytest.raises(updater.UpdateError, match="Prüfsumme"):
        updater.expected_sha256(bare, "screenkaffee-Windows-x64.zip")


def test_extract_rejects_paths_outside_and_handles_tar(tmp_path):
    evil = tmp_path / "evil.zip"
    evil.write_bytes(make_zip({"../../boese.txt": b"x"}))
    with pytest.raises(updater.UpdateError, match="ungültige"):
        updater.extract(evil, tmp_path / "out")
    archive = tmp_path / "linux.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("screenkaffee/screenkaffee")
        info.mode, info.size = 0o755, 9
        tar.addfile(info, io.BytesIO(b"#!/bin/sh"))
    package = updater.extract(archive, tmp_path / "tar")
    assert (package / "screenkaffee").read_bytes() == b"#!/bin/sh"


def test_prepare_stages_the_new_version(tmp_path):
    data = make_zip({"screenkaffee/screenkaffee.exe": b"neu", "screenkaffee/_internal/lib.dll": b"dll"})
    opener = fake_github(release_payload(data))
    release = updater.latest_release("o/r", opener)
    package = updater.prepare(release, "windows-x64", tmp_path, opener=opener)
    assert package == (tmp_path / "_update" / "new" / "screenkaffee").resolve()
    assert (package / "_internal" / "lib.dll").read_bytes() == b"dll"
    assert json.loads((tmp_path / "_update" / "release.json").read_text(encoding="utf-8"))["tag"] == "v0.7.0"
    assert not (tmp_path / "_update" / "download" / "screenkaffee-Windows-x64.zip").exists()


def staged_app(tmp_path):
    root = tmp_path / "app"
    (root / "_internal").mkdir(parents=True)
    (root / "_internal" / "alt.dll").write_text("alt")
    (root / "screenkaffee.exe").write_text("alt")
    (root / "data").mkdir()
    (root / "data" / "settings.json").write_text("{\"meins\": 1}")
    package = root / "_update" / "new" / "screenkaffee"
    (package / "_internal").mkdir(parents=True)
    (package / "_internal" / "neu.dll").write_text("neu")
    (package / "screenkaffee.exe").write_text("neu")
    (package / "data").mkdir()
    (package / "data" / "settings.json").write_text("{}")         # darf nie die Nutzerdaten überschreiben
    (root / "_update" / "release.json").write_text(json.dumps({"tag": "v0.7.0", "notes": "- Updater"}), encoding="utf-8")
    return root, package


def test_apply_swaps_program_files_and_keeps_data(tmp_path, monkeypatch):
    root, package = staged_app(tmp_path)
    waited = []
    updater.apply(package, root, pid=4711, wait=waited.append)
    assert waited == [4711]
    assert (root / "screenkaffee.exe").read_text() == "neu"
    assert sorted(p.name for p in (root / "_internal").iterdir()) == ["neu.dll"]      # keine alten Reste
    assert (root / "data" / "settings.json").read_text() == "{\"meins\": 1}"
    assert (root / "_update" / "old" / "screenkaffee.exe").read_text() == "alt"
    monkeypatch.setattr(updater, "application_root", lambda: root)
    info = updater.finish_previous()
    assert info == {"tag": "v0.7.0", "notes": "- Updater"} and not (root / "_update").exists()
    assert updater.finish_previous() is None


def test_broken_release_notes_do_not_mark_the_update_as_failed(tmp_path):
    root, package = staged_app(tmp_path)
    (root / "_update" / "release.json").write_text("{kaputt", encoding="utf-8")
    updater.apply(package, root)
    assert (root / "screenkaffee.exe").read_text() == "neu"
    assert updater.finish_previous(root) == {}


def test_apply_rolls_back_on_error(tmp_path, monkeypatch):
    root, package = staged_app(tmp_path)
    def broken(*args, **kwargs):
        raise OSError("Datenträger voll")
    monkeypatch.setattr(updater.shutil, "copy2", broken)
    with pytest.raises(OSError):
        updater.apply(package, root)
    assert (root / "screenkaffee.exe").read_text() == "alt"
    assert (root / "_internal" / "alt.dll").read_text() == "alt"
    assert not (root / "_update" / "done.json").exists()


def test_failed_update_is_reported_on_next_start(tmp_path):
    (tmp_path / "_update").mkdir()
    (tmp_path / "_update" / "update-error.log").write_text("PermissionError: gesperrt\n", encoding="utf-8")
    assert updater.finish_previous(tmp_path) == {"error": "PermissionError: gesperrt"}


def test_helper_command_runs_the_new_package(tmp_path):
    command = updater.helper_command(tmp_path / "neu", "linux-x86_64", tmp_path / "app", 42, tmp_path / "daten")
    assert command == [str(tmp_path / "neu" / "screenkaffee"), "--apply-update", str(tmp_path / "app"),
                       "--wait-pid", "42", "--relaunch-data", str(tmp_path / "daten")]
    assert updater.helper_command(tmp_path, "windows-arm64", tmp_path, 1)[0].endswith("screenkaffee.exe")


def test_old_token_file_is_forgotten(tmp_path):
    old = tmp_path / "github-token.bin"
    old.write_bytes(b"alt")
    updater.forget_token(tmp_path)
    assert not old.exists()
    updater.forget_token(tmp_path)              # fehlt schon: kein Fehler


# --- Oberfläche ----------------------------------------------------------------------------------------------------

def window_for(tmp_path):
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    return MainWindow(Workspace(tmp_path))


def test_whats_new_dialog_after_update(qt_app, tmp_path):
    window = window_for(tmp_path)
    dialog = window.show_update_result({"tag": "v0.7.0", "notes": "## Neu\n- Selbst-Update"})
    from screenkaffee import __version__
    assert dialog.windowTitle() == f"Neu in screenkaffee {__version__}"
    from PySide6.QtWidgets import QTextBrowser
    assert "Selbst-Update" in dialog.findChild(QTextBrowser).toPlainText()
    dialog.close()


def test_startup_shows_notes_and_respects_update_mode(qt_app, tmp_path, monkeypatch):
    window = window_for(tmp_path / "daten")
    root = tmp_path / "app"
    (root / "_update").mkdir(parents=True)
    (root / "_update" / "done.json").write_text(json.dumps({"tag": "v0.7.0", "notes": "- Neu"}), encoding="utf-8")
    monkeypatch.setattr(updater, "application_root", lambda: root)
    shown, checks = [], []
    monkeypatch.setattr(window, "show_update_result", shown.append)
    monkeypatch.setattr(window, "check_updates", lambda **kw: checks.append(kw))
    from PySide6.QtCore import QTimer
    monkeypatch.setattr(QTimer, "singleShot", lambda ms, fn: fn())
    window.settings["update_mode"] = "off"
    window.startup_update()
    assert shown == [{"tag": "v0.7.0", "notes": "- Neu"}] and checks == []
    window.settings["update_mode"] = "auto"
    window.startup_update()
    assert checks == [{"auto": True, "quiet": True}]


def test_update_check_messages(qt_app, tmp_path, monkeypatch):
    window = window_for(tmp_path)
    old = updater.Release("v0.0.1", (0, 0, 1))
    window.update_checked(old, quiet=True)
    assert "ist aktuell" in window.statusBar().currentMessage()
    window.update_checked(updater.UpdateError("GitHub ist nicht erreichbar"), quiet=True)
    assert "nicht erreichbar" in window.statusBar().currentMessage()
    monkeypatch.setattr(updater, "installed_variant", lambda root=None: None)
    window.update_checked(updater.Release("v99.0.0", (99, 0, 0)), quiet=True)
    assert "git pull" in window.statusBar().currentMessage()
    downloads = []
    monkeypatch.setattr(updater, "installed_variant", lambda root=None: "windows-x64")
    monkeypatch.setattr(window, "download_update", lambda *args: downloads.append(args))
    window.update_checked(updater.Release("v99.0.0", (99, 0, 0)), auto=True, quiet=True)
    assert downloads and downloads[0][1:] == ("windows-x64", True)


def test_ready_update_waits_for_open_editor_and_runs_on_quit(qt_app, tmp_path, monkeypatch):
    window = window_for(tmp_path)
    window.pending_update = (tmp_path / "neu", "windows-x64", "v0.7.0")
    window.editors.append(object())
    window.restart_for_update()
    assert "beim Beenden" in window.statusBar().currentMessage()
    window.editors.clear()
    launched = []
    monkeypatch.setattr(updater, "launch_helper", launched.append)
    monkeypatch.setattr(updater, "application_root", lambda: tmp_path / "app")
    window.launch_pending_update()
    import os
    assert launched[0][1:5] == ["--apply-update", str(tmp_path / "app"), "--wait-pid", str(os.getpid())]
    assert window.pending_update is None
