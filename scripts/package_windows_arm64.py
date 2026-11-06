"""Build a movable native Windows ARM64 archive (dist/screenkaffee-Windows-arm64.zip) on any Windows host."""
import argparse
import hashlib
import io
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-arm64.zip"
RUNTIME_SHA256 = "3065efc3d382d1cda66757ac71ade11904fa6e350f5a97eb74811acd71ba5532"

ARM64_PACKAGES = [
    "PySide6-Essentials==6.12.0",
    "PySide6-Addons==6.12.0",
    "numpy>=2.1,<3",
    "Pillow>=11,<13",
    "pillow-jxl-plugin>=1.3,<2",
    "zxing-cpp>=3,<4",
    "pypdfium2>=4,<6",
    "winrt-runtime==3.2.1",
    "winrt-Windows.Graphics.Capture==3.2.1",
    "winrt-Windows.Graphics.Capture.Interop==3.2.1",
    "winrt-Windows.Graphics.DirectX==3.2.1",
    "winrt-Windows.Graphics.DirectX.Direct3D11==3.2.1",
    "winrt-Windows.Graphics.DirectX.Direct3D11.Interop==3.2.1",
    "winrt-Windows.Graphics==3.2.1",
    "winrt-Windows.Foundation==3.2.1",
    "winrt-Windows.Foundation.Collections==3.2.1",
]

MAIN_SCRIPT = """import os
from pathlib import Path
import sys

appdir = Path(__file__).resolve().parent.parent
os.environ.setdefault("SCREENKAFFEE_APPDIR", str(appdir))
os.environ.setdefault("SCREENKAFFEE_DATA", str(appdir / "data"))
sys.path.insert(0, str(appdir / "source"))
if __name__ == "__main__":
    try:
        from screenkaffee.__main__ import main
        raise SystemExit(main())
    except Exception:
        import traceback
        folder = Path(os.environ.get("SCREENKAFFEE_DATA", appdir / "data"))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        if "--self-test" in sys.argv or "--diagnose" in sys.argv:
            raise SystemExit(1)
        raise
"""


def build_arm64_launcher() -> bytes:
    try:
        import distlib  # eigenständiges Paket, funktioniert auch in Umgebungen ohne pip (uv, CI)
    except ImportError:
        import pip._vendor.distlib as distlib
    stub = (Path(distlib.__file__).resolve().parent / "w64-arm.exe").read_bytes()
    shebang = b"#!<launcher_dir>\\runtime\\pythonw.exe\r\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("__main__.py", MAIN_SCRIPT.encode("utf-8"))
    return stub + shebang + buf.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-wheels", type=Path)
    args = parser.parse_args()
    cache = ROOT / ".tools" / "windows-arm64-build"
    cache.mkdir(parents=True, exist_ok=True)
    runtime = cache / "python-3.12.10-embed-arm64.zip"
    if not runtime.exists():
        print("Downloading official CPython 3.12.10 win_arm64 embeddable runtime…", flush=True)
        urllib.request.urlretrieve(RUNTIME_URL, runtime)
    if hashlib.sha256(runtime.read_bytes()).hexdigest() != RUNTIME_SHA256:
        raise RuntimeError("CPython ARM64 SHA256 mismatch; refusing to package")
    site = args.reuse_wheels.resolve() if args.reuse_wheels else cache / ("wheels-" + uuid.uuid4().hex[:8])
    command = [
        sys.executable, "-m", "pip", "install", "--no-compile", "--target", str(site),
        "--platform", "win_arm64", "--only-binary=:all:",
        "--implementation", "cp", "--python-version", "3.12",
        "--abi", "cp312", "--abi", "abi3", "--abi", "none",
        *ARM64_PACKAGES,
    ]
    if args.reuse_wheels is None:
        subprocess.run(command, check=True)
        # There is no win_arm64 wheel of imageio-ffmpeg (and none of PyAV, which only faster-whisper needs, nor of
        # PyAudioWPatch: no sound recording on ARM64). Its win_amd64 wheel carries an x64 ffmpeg.exe, which
        # Windows 11 on ARM runs under emulation, so video recording and conversion keep working.
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-compile", "--no-deps", "--target", str(site),
                        "--platform", "win_amd64", "--only-binary=:all:", "--implementation", "cp", "--python-version", "3.12",
                        "--abi", "cp312", "--abi", "abi3", "--abi", "none", "imageio-ffmpeg>=0.6,<1"], check=True)
    target = ROOT / "dist" / "screenkaffee-Windows-arm64.zip"
    target.parent.mkdir(exist_ok=True)
    prefix = "screenkaffee"
    print("Assembling Windows ARM64 archive…", flush=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        with zipfile.ZipFile(runtime, "r") as rt:
            for item in rt.infolist():
                if item.filename.endswith("._pth"):
                    pth = "python312.zip\n.\n../source\nsite-packages\nimport site\n"
                    output.writestr(f"{prefix}/runtime/{item.filename}", pth.encode("utf-8"))
                else:
                    output.writestr(f"{prefix}/runtime/{item.filename}", rt.read(item.filename))
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from package_prune import drop_unresolved, keep_site
        files = [path for path in site.rglob("*") if path.is_file() and "__pycache__" not in path.parts
                 and keep_site(path.relative_to(site).as_posix())
                 and path.relative_to(site).as_posix() not in ("icuuc.dll", "icudt78.dll")]
        files, _ = drop_unresolved(files, site)
        for path in files:
            output.write(path, f"{prefix}/runtime/site-packages/{path.relative_to(site).as_posix()}")
        for path in (ROOT / "screenkaffee").rglob("*.py"):
            output.write(path, f"{prefix}/source/screenkaffee/{path.relative_to(ROOT / 'screenkaffee').as_posix()}")
        for name in ["launch.py", "requirements.txt", "pyproject.toml"]:
            output.write(ROOT / name, f"{prefix}/source/{name}")
        for name in ["README.md", "FEATURES.md", "LICENSE", "THIRD_PARTY.md"]:
            output.write(ROOT / name, f"{prefix}/{name}")
        if (ROOT / "licenses").is_dir():
            for path in (ROOT / "licenses").rglob("*"):
                if path.is_file():
                    output.write(path, f"{prefix}/licenses/{path.relative_to(ROOT / 'licenses').as_posix()}")
        for dist_info in site.glob("*.dist-info"):
            pkg_name = dist_info.name.split("-", 1)[0]
            for file in dist_info.rglob("*"):
                if file.is_file() and any(x in file.name.lower() for x in ("license", "copying", "notice")):
                    rel = file.relative_to(dist_info).as_posix()
                    output.write(file, f"{prefix}/licenses/{pkg_name}/{rel}")
        deps = []
        for dist_info in sorted(site.glob("*.dist-info")):
            parts = dist_info.name[:-len(".dist-info")].split("-", 1)
            if len(parts) == 2:
                deps.append(f"{parts[0]} {parts[1]}")
        output.writestr(f"{prefix}/licenses/DEPENDENCIES.txt", ("\n".join(deps) + "\n").encode("utf-8"))
        if (ROOT / "models").is_dir():
            for path in (ROOT / "models").rglob("*"):
                # OCR ist aus der App entfernt; Untertitel-Erkennung gibt es auf ARM64 nicht (kein faster-whisper).
                if path.is_file() and "tessdata" not in path.parts and "whisper-base" not in path.parts:
                    output.write(path, f"{prefix}/models/{path.relative_to(ROOT / 'models').as_posix()}")
        output.writestr(f"{prefix}/addons.json", "{}\n")
        output.writestr(f"{prefix}/screenkaffee.exe", build_arm64_launcher())
        output.writestr(
            f"{prefix}/Start-screenkaffee.cmd",
            '@echo off\r\nset "SCREENKAFFEE_APPDIR=%~dp0"\r\nset "SCREENKAFFEE_DATA=%~dp0data"\r\nstart "" "%~dp0runtime\\pythonw.exe" "%~dp0source\\launch.py" %*\r\n',
        )
        output.writestr(
            f"{prefix}/Start-mit-bisherigen-Aufnahmen.cmd",
            '@echo off\r\nstart "" "%~dp0screenkaffee.exe" --data-dir "%~dp0..\\screenkaffee\\data"\r\n',
        )
    print(target, flush=True)


if __name__ == "__main__":
    main()
