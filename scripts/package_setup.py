"""Baut beide Setups aus den fertigen Windows-Archiven."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
ASSETS = {variant: f"screenkaffee-Setup-Windows-{variant}.exe" for variant in ("x64", "arm64")}


def find_iscc():
    candidates = [os.environ.get("SCREENKAFFEE_ISCC"), r"D:\Tools\InnoSetup\ISCC.exe",
                  str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inno Setup 6/ISCC.exe"),
                  str(Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Inno Setup 6/ISCC.exe"),
                  str(Path(os.environ.get("PROGRAMFILES", "")) / "Inno Setup 6/ISCC.exe")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError("Inno Setup: ISCC.exe nicht gefunden (SCREENKAFFEE_ISCC setzen).")


def build(variant, archive, output, iscc=None):
    archive, output = Path(archive), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    version = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "screenkaffee/__init__.py").read_text(encoding="utf-8")).group(1)
    with tempfile.TemporaryDirectory(prefix="screenkaffee-setup-") as tmp:
        destination = Path(tmp).resolve()
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                if not (destination / member.filename).resolve().is_relative_to(destination):
                    raise ValueError("Ungültiger Archivpfad")
            package.extractall(destination)
        source = destination / "screenkaffee"
        required = source / ("_internal" if variant == "x64" else "runtime/pythonw.exe")
        if not required.exists() or not (source / "screenkaffee.exe").is_file():
            raise ValueError(f"Ungültiger Paketaufbau für {variant}")
        arch = "x64compatible" if variant == "x64" else "arm64"
        command = [str(iscc or find_iscc()), f"/DAppVersion={version}", f"/DSourceDir={source}",
                   f"/DOutputDir={output}", f"/DVariant={variant}", f"/DArchitecture={arch}",
                   str(ROOT / "installer/screenkaffee.iss")]
        subprocess.run(command, check=True)
    result = output / ASSETS[variant]
    def digest(path):
        with path.open("rb") as f:
            return hashlib.file_digest(f, "sha256").hexdigest()
    if result.is_file():
        manifest = {"variant": variant, "archive_sha256": digest(archive), "setup_sha256": digest(result)}
        (output / (result.name + ".json")).write_text(json.dumps(manifest), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=ASSETS, action="append")
    parser.add_argument("--archives", type=Path, default=ROOT / "dist")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    for variant in args.variant or ASSETS:
        build(variant, args.archives / f"screenkaffee-Windows-{variant}.zip", args.output)


if __name__ == "__main__":
    main()
