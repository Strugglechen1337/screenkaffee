"""Build an offline Linux x86_64 tarball on any host, preserving ELF/symlinks.

The official relocatable CPython runtime is checked against its published SHA256.
Linux wheels are resolved by pip with explicit platform and interpreter tags.
No Linux system installation and no extraction of untrusted tar paths is used.
"""
from pathlib import Path
import copy
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import urllib.request
import uuid
import argparse

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_URL = "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16%2B20261026-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
RUNTIME_SHA256 = "ce4f6d0a6cfdb3bed6e44c2f9affd85943c76a42e25ad3a259002f31d6fb8a20"
LAUNCHER = '''#!/bin/sh
set -eu
APPDIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PYTHONNOUSERSITE=1
export PYTHONPATH="$APPDIR/source"
export SCREENKAFFEE_APPDIR="$APPDIR"
export SCREENKAFFEE_DATA="${SCREENKAFFEE_DATA:-$APPDIR/data}"
export LD_LIBRARY_PATH="$APPDIR/runtime/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
# Qt chooses Wayland under Hyprland; XCB remains available for X11.
if [ -n "${WAYLAND_DISPLAY:-}" ]; then
  export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland;xcb}"
fi
exec "$APPDIR/runtime/bin/python3.12" -m screenkaffee "$@"
'''


def executable_wheels(info):
    if "/imageio_ffmpeg/binaries/ffmpeg" in info.name or info.name.endswith(".so"):
        info.mode |= 0o111
    return info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-wheels", type=Path)
    args = parser.parse_args()
    cache = ROOT / ".tools" / "linux-build"
    cache.mkdir(parents=True, exist_ok=True)
    runtime = cache / "cpython-linux.tar.gz"
    if not runtime.exists():
        print("Downloading official standalone CPython…", flush=True)
        with urllib.request.urlopen(RUNTIME_URL, timeout=90) as response, runtime.open("wb") as out:
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
    if hashlib.sha256(runtime.read_bytes()).hexdigest() != RUNTIME_SHA256:
        raise RuntimeError("CPython SHA256 mismatch; refusing to package")
    # Fresh staging avoids deleting files held by Windows sync/indexing services.
    site = args.reuse_wheels.resolve() if args.reuse_wheels else cache / ("wheels-" + uuid.uuid4().hex[:8])
    command = [sys.executable, "-m", "pip", "install", "--no-compile", "--target", str(site),
        *[value for version in range(34, 16, -1) for value in ("--platform", f"manylinux_2_{version}_x86_64")],
        "--platform", "manylinux2014_x86_64", "--only-binary=:all:",
        "--implementation", "cp", "--python-version", "3.12", "--abi", "cp312", "--abi", "abi3", "--abi", "none",
        "PySide6-Essentials==6.12.0", "PySide6-Addons==6.12.0", "numpy>=2.1,<3", "Pillow>=11,<13", "pillow-jxl-plugin>=1.3,<2", "zxing-cpp>=3,<4",
        "imageio-ffmpeg>=0.6,<1", "faster-whisper>=1.2,<2", "av>=14,<16", "pypdfium2>=4,<6"]
    if args.reuse_wheels is None:
        subprocess.run(command, check=True)
    elif not (site / "PySide6" / "QtWidgets.abi3.so").is_file():
        raise ValueError("Keine passenden Linux-Qt-Wheels im Staging-Ordner.")
    target = ROOT / "dist" / "screenkaffee-Linux-x86_64.tar.gz"
    target.parent.mkdir(exist_ok=True)
    prefix = "screenkaffee"
    print("Assembling Linux runtime, Qt and application…", flush=True)
    with tarfile.open(target, "w:gz", compresslevel=6) as output:
        with tarfile.open(runtime, "r:gz") as source:
            for member in source:
                if not member.name.startswith("python/") and member.name != "python":
                    raise RuntimeError("Unexpected runtime archive layout")
                member = copy.copy(member)
                stream = source.extractfile(member) if member.isfile() else None
                member.name = prefix + "/runtime" + member.name[len("python"):]
                if member.islnk() and member.linkname.startswith("python/"):
                    member.linkname = prefix + "/runtime/" + member.linkname[len("python/"):]
                output.addfile(member, stream)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from package_prune import drop_unresolved, keep_site
        files = [path for path in site.rglob("*") if path.is_file() and "__pycache__" not in path.parts
                 and keep_site(path.relative_to(site).as_posix())]
        files, _ = drop_unresolved(files, site)
        # Untertitel-Erkennung (Modell + ctranslate2/PyAV/tokenizers/faster_whisper) als eigenes Archiv.
        from package_addons import SPEECH_ASSETS, is_speech_site_path, model_entries, write_addon
        site_prefix = "runtime/lib/python3.12/site-packages/"
        speech = [(site_prefix + path.relative_to(site).as_posix(), path) for path in files
                  if is_speech_site_path(path.relative_to(site).as_posix())]
        info = write_addon(speech + model_entries(ROOT), ROOT / "dist" / SPEECH_ASSETS["linux-x86_64"])
        moved = {path for _, path in speech}
        for path in files:
            if path not in moved:
                output.add(path, prefix + "/" + site_prefix + path.relative_to(site).as_posix(), filter=executable_wheels)
        content = json.dumps(info, indent=2).encode("utf-8")
        addons = tarfile.TarInfo(prefix + "/addons.json")
        addons.size = len(content)
        output.addfile(addons, io.BytesIO(content))
        for path in (ROOT / "screenkaffee").rglob("*.py"):
            output.add(path, prefix + "/source/screenkaffee/" + path.relative_to(ROOT / "screenkaffee").as_posix())
        for filename in ["README.md", "FEATURES.md", "LICENSE", "THIRD_PARTY.md", "requirements.txt"]:
            output.add(ROOT / filename, prefix + "/" + filename)
        if (ROOT / "licenses").is_dir():
            output.add(ROOT / "licenses", prefix + "/licenses")
        launcher = tarfile.TarInfo(prefix + "/screenkaffee")
        content = LAUNCHER.encode()
        launcher.mode, launcher.size = 0o755, len(content)
        output.addfile(launcher, io.BytesIO(content))
    manifest = {"runtime_url": RUNTIME_URL, "runtime_sha256": RUNTIME_SHA256,
                "runtime_version": "3.12.16", "architecture": "x86_64", "minimum_glibc": "2.34",
                "linux_execution_tested": False}
    target.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(target, flush=True)


if __name__ == "__main__":
    main()
