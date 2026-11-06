"""Build a movable Windows folder, with source and dependency license texts."""
from pathlib import Path
import importlib.metadata as md
import json
import shutil
import subprocess
import sys
import zipfile
import uuid
import argparse

ROOT = Path(__file__).resolve().parent.parent


def bundle_licenses(target):
    folder = Path(target) / "licenses"
    folder.mkdir(exist_ok=True)
    for name in ["PySide6-Essentials", "PySide6-Addons", "shiboken6", "numpy", "Pillow", "pillow-jxl-plugin", "zxing-cpp", "winrt-runtime",
        "imageio-ffmpeg", "faster-whisper", "av", "pypdfium2", "ctranslate2", "onnxruntime", "tokenizers", "PyAudioWPatch",
        "winrt-Windows.Graphics.Capture", "winrt-Windows.Graphics.Capture.Interop",
        "winrt-Windows.Graphics.DirectX", "winrt-Windows.Graphics.DirectX.Direct3D11",
        "winrt-Windows.Graphics.DirectX.Direct3D11.Interop", "winrt-Windows.Graphics",
        "winrt-Windows.Foundation", "winrt-Windows.Foundation.Collections"]:
        distribution = md.distribution(name)
        for file in distribution.files or []:
            if any(x in str(file).lower() for x in ["license", "copying", "notice"]):
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    dest = folder / name / str(file).replace("..", "_")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)
    (folder / "DEPENDENCIES.txt").write_text("\n".join(f"{n} {md.version(n)}" for n in ["PySide6-Essentials", "shiboken6", "numpy", "Pillow", "zxing-cpp", "winrt-runtime"]), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-stage", type=Path, help="Repackage an already compiled staging folder")
    parser.add_argument("--app-folder", default="screenkaffee", help="Zielordner unter dist; für Updates einer laufenden App separat wählen")
    args = parser.parse_args()
    import re
    if not re.fullmatch(r"screenkaffee(?:-[a-z0-9-]+)?", args.app_folder):
        raise ValueError("Ungültiger App-Ordnername.")
    staging = args.reuse_stage.resolve() if args.reuse_stage else ROOT / ".tools" / ("windows-package-" + uuid.uuid4().hex[:8])
    if not staging.resolve().is_relative_to((ROOT / ".tools").resolve()):
        raise ValueError("Staging muss im .tools-Verzeichnis des Projekts liegen.")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed", "--onedir",
        "--distpath", str(staging),
        "--name", "screenkaffee", "--collect-submodules", "winrt", "--hidden-import", "zxingcpp", "--collect-all", "pillow_jxl",
        "--exclude-module", "tkinter", "--exclude-module", "matplotlib", "--exclude-module", "scipy",
        "--exclude-module", "pandas", "--exclude-module", "pytest", "--exclude-module", "tesserocr",
        "--exclude-module", "onnxruntime", "--exclude-module", "hf_xet", str(ROOT / "launch.py")]
    if args.reuse_stage is None:
        subprocess.run(command, cwd=ROOT, check=True)
    target = staging / "screenkaffee"
    # Qt uses the unversioned ICU API supplied by Windows 10+. The authoring
    # runtime also carries an unrelated ICU with suffixed exports. PyInstaller
    # can discover that DLL via PATH; shipping it would break Qt at startup.
    for name in ("icuuc.dll", "icudt78.dll", "icuuc.dll.excluded"):
        (target / "_internal" / name).unlink(missing_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from package_prune import prune_tree
    print(f"Nicht genutzte Qt- und Python-Teile entfernt: {prune_tree(target / '_internal') / 1e6:.0f} MB", flush=True)
    bundle_licenses(target)
    if (ROOT / "licenses").is_dir():
        shutil.copytree(ROOT / "licenses", target / "licenses", dirs_exist_ok=True)
    for name in ["README.md", "FEATURES.md", "LICENSE", "THIRD_PARTY.md"]:
        shutil.copy2(ROOT / name, target / name)
    source = target / "source"
    shutil.copytree(ROOT / "screenkaffee", source / "screenkaffee", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    for name in ["launch.py", "requirements.txt", "pyproject.toml"]:
        shutil.copy2(ROOT / name, source / name)
    (target / "tools").mkdir(exist_ok=True)
    # Untertitel-Erkennung (Modell + ctranslate2/PyAV/tokenizers) als eigenes, nur auf Wunsch geladenes Archiv.
    from package_addons import SPEECH_ASSETS, is_speech_site_path, model_entries, write_addon
    internal = target / "_internal"
    speech = [(path.relative_to(target).as_posix(), path) for path in internal.rglob("*")
              if path.is_file() and is_speech_site_path(path.relative_to(internal).as_posix())]
    info = write_addon(speech + model_entries(ROOT), ROOT / "dist" / SPEECH_ASSETS["windows-x64"])
    (target / "addons.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    for _, path in speech:
        path.unlink()
    for folder in sorted({p.parent for _, p in speech}, key=lambda f: len(f.parts), reverse=True):
        while folder != internal and folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
            folder = folder.parent
    print(f"Untertitel-Erweiterung ausgelagert: {info['speech']['bytes'] / 1e6:.0f} MB", flush=True)
    (target / "Start-mit-bisherigen-Aufnahmen.cmd").write_text(
        '@echo off\nstart "" "%~dp0screenkaffee.exe" --data-dir "%~dp0..\\screenkaffee\\data"\n', encoding="utf-8")
    subprocess.run([str(target / "screenkaffee.exe"), "--self-test", "--data-dir", str(staging / "validation")],
                   timeout=30, check=True)
    archive = ROOT / "dist" / "screenkaffee-Windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in target.rglob("*"):
            if path.is_file() and "data" not in path.relative_to(target).parts:
                output.write(path, "screenkaffee/" + path.relative_to(target).as_posix())
    # Never let PyInstaller recursively clean an app folder containing user data.
    # Copy out of fresh staging; existing data/ remains untouched.
    destination = ROOT / "dist" / args.app_folder
    shutil.copytree(target, destination, dirs_exist_ok=True)
    for name in ("icuuc.dll", "icudt78.dll", "icuuc.dll.excluded"):
        (destination / "_internal" / name).unlink(missing_ok=True)
    print(archive)
    print(destination)


if __name__ == "__main__":
    main()
