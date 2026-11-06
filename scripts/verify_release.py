"""Verify source parity, PE/ELF layout and a relocated Windows release across x64, ARM64 and Linux."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def pe_machine(data: bytes) -> int:
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    return struct.unpack_from("<H", data, pe_offset + 4)[0]


def main():
    windows = ROOT / "dist" / "screenkaffee-Windows-x64.zip"
    windows_arm64 = ROOT / "dist" / "screenkaffee-Windows-arm64.zip"
    linux = ROOT / "dist" / "screenkaffee-Linux-x86_64.tar.gz"
    speech_windows = ROOT / "dist" / "screenkaffee-Untertitel-Windows-x64.zip"
    speech_linux = ROOT / "dist" / "screenkaffee-Untertitel-Linux-x86_64.zip"
    report = {}
    with zipfile.ZipFile(windows) as win, zipfile.ZipFile(windows_arm64) as arm, tarfile.open(linux) as lin:
        for source in (ROOT / "screenkaffee").glob("*.py"):
            current = source.read_bytes()
            assert win.read("screenkaffee/source/screenkaffee/" + source.name) == current, source.name
            assert arm.read("screenkaffee/source/screenkaffee/" + source.name) == current, source.name
            assert lin.extractfile("screenkaffee/source/screenkaffee/" + source.name).read() == current, source.name
        assert "screenkaffee/_internal/icuuc.dll" not in win.namelist()
        assert "screenkaffee/runtime/site-packages/icuuc.dll" not in arm.namelist()
        assert pe_machine(win.read("screenkaffee/screenkaffee.exe")) == 0x8664
        assert pe_machine(arm.read("screenkaffee/screenkaffee.exe")) == 0xAA64
        assert pe_machine(arm.read("screenkaffee/runtime/pythonw.exe")) == 0xAA64
        assert pe_machine(arm.read("screenkaffee/runtime/site-packages/PySide6/QtWidgets.pyd")) == 0xAA64
        assert pe_machine(arm.read("screenkaffee/runtime/site-packages/pillow_jxl/pillow_jxl.cp312-win_arm64.pyd")) == 0xAA64
        assert lin.extractfile("screenkaffee/runtime/bin/python3.12").read(4) == b"\x7fELF"
        assert lin.getmember("screenkaffee/screenkaffee").mode & 0o111
        assert not any(n.endswith(".pyd") for n in lin.getnames())
        assert "screenkaffee/licenses/GPL-3.0.txt" in win.namelist()
        assert "screenkaffee/licenses/GPL-3.0.txt" in arm.namelist()
        assert "screenkaffee/licenses/DEPENDENCIES.txt" in win.namelist()
        assert "screenkaffee/licenses/DEPENDENCIES.txt" in arm.namelist()
        assert "screenkaffee/licenses/LGPL-3.0.txt" in lin.getnames()
        assert b'SCREENKAFFEE_APPDIR="$APPDIR"' in lin.extractfile("screenkaffee/screenkaffee").read()
        binaries = [item for item in lin.getmembers() if "/imageio_ffmpeg/binaries/ffmpeg-" in item.name and item.isfile()]
        assert binaries and all(item.mode & 0o111 for item in binaries)
        assert any("/imageio_ffmpeg/binaries/ffmpeg-" in name for name in arm.namelist())
        for name in ("pypdfium2", "pillow_jxl"):
            assert any("/site-packages/" + name + "/" in path for path in lin.getnames()), name
        # Untertitel-Erkennung steckt nicht mehr in den Hauptpaketen, sondern in eigenen Archiven mit passendem Fingerabdruck.
        expected = hashlib.sha256((ROOT / "models/whisper-base/model.bin").read_bytes()).digest()
        assert json.loads(arm.read("screenkaffee/addons.json")) == {}
        for names, addon_path, info in ((win.namelist(), speech_windows, json.loads(win.read("screenkaffee/addons.json"))),
                                        (lin.getnames(), speech_linux, json.loads(lin.extractfile("screenkaffee/addons.json").read()))):
            assert not any("whisper-base" in n or "/ctranslate2/" in n or "/av.libs/" in n for n in names)
            with zipfile.ZipFile(addon_path) as addon:
                meta = json.loads(addon.read("addon.json"))
                assert info["speech"]["asset"] == addon_path.name and info["speech"]["fingerprint"] == meta["fingerprint"]
                assert hashlib.sha256(addon.read("screenkaffee/models/whisper-base/model.bin")).digest() == expected
                assert len(meta["files"]) == info["speech"]["files"] and all(not n.startswith(("/", "..")) for n in addon.namelist())
                assert any("/ctranslate2/" in n for n in meta["files"]) and any(n.startswith(("_internal/av", "runtime/lib/python3.12/site-packages/av")) for n in meta["files"])
        assert not any("whisper-base" in n for n in arm.namelist())
        report["source_matches_packages"] = True
        report["windows_arm64_pe_layout"] = True
        report["linux_elf_layout"] = True
        report["linux_ffmpeg_executable"] = True
        report["speech_addons_match"] = True
        if os.name == "nt":
            temporary = tempfile.TemporaryDirectory(prefix="prism-portable-check-")
            destination = Path(temporary.name).resolve()
            # Explicitly validate the cleanup target before TemporaryDirectory
            # performs its eventual recursive deletion.
            assert destination.parent == Path(tempfile.gettempdir()).resolve()
            assert destination.name.startswith("prism-portable-check-")
            try:
                for item in win.infolist():
                    assert not Path(item.filename).is_absolute()
                    assert ".." not in Path(item.filename).parts
                for item in arm.infolist():
                    assert not Path(item.filename).is_absolute()
                    assert ".." not in Path(item.filename).parts
                win.extractall(destination)
                exe = destination / "screenkaffee" / "screenkaffee.exe"
                data = destination / "validation"
                env = dict(os.environ)
                env.pop("PYTHONPATH", None)
                env.pop("PYTHONHOME", None)
                subprocess.run([str(exe), "--self-test", "--data-dir", str(data)],
                               env=env, timeout=30, check=True)
                assert (data / "self-test-ok.txt").is_file()
                subprocess.run([str(exe), "--diagnose", "--data-dir", str(data)],
                               env=env, timeout=30, check=True)
                diagnostics = json.loads((data / "diagnostics.json").read_text(encoding="utf-8"))
                assert diagnostics.get("capture") and not diagnostics.get("capture_error")
                report["relocated_windows_self_test"] = True
                # Erweiterung wie die App verlinken und eine echte Spracherkennung im gepackten Programm laufen lassen.
                import sys
                sys.path.insert(0, str(ROOT))
                from screenkaffee import addons
                with zipfile.ZipFile(speech_windows) as addon:
                    meta = json.loads(addon.read("addon.json"))
                    cache = addons.cache_dir(data, meta["fingerprint"])
                    addon.extractall(addons.long_path(cache))
                addons.link_into(cache, destination / "screenkaffee")
                subprocess.run([str(exe), "--self-test", "--speech-test", "--data-dir", str(data)],
                               env=env, timeout=120, check=True)
                assert (data / "self-test-speech-ok.txt").is_file()
                report["relocated_windows_speech_addon"] = True
                report["relocated_windows_capture"] = [m["backend"] for m in diagnostics["capture"]]
            finally:
                temporary.cleanup()
    report["artifacts"] = [{"name": p.name, "bytes": p.stat().st_size,
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in (windows, windows_arm64, linux, speech_windows, speech_linux)]
    report["windows_arm64_execution_tested"] = False
    report["linux_execution_tested"] = False
    report["physical_hdr_tested"] = False
    (ROOT / "dist" / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
