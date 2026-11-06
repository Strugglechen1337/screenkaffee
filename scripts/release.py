"""Alles in einem Schritt: Tests, drei Pakete bauen, verifizieren, optional zu GitHub hochladen.

Aufruf (Windows, im Projektordner):
    .\\.venv\\Scripts\\python.exe scripts\\release.py --tag v0.6.0            # bauen + prüfen
    .\\.venv\\Scripts\\python.exe scripts\\release.py --tag v0.6.0 --upload   # zusätzlich hochladen (gh nötig)
Nichts wird automatisch hochgeladen; --upload ist Pflicht und fragt vorher nach.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
ARCHIVES = ["screenkaffee-Windows-x64.zip", "screenkaffee-Windows-arm64.zip", "screenkaffee-Linux-x86_64.tar.gz",
            "screenkaffee-Untertitel-Windows-x64.zip", "screenkaffee-Untertitel-Linux-x86_64.zip", "verification.json"]


def run(*args):
    print("\n>>", " ".join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tag", required=True, help="GitHub-Release-Tag, z. B. v0.6.0")
    parser.add_argument("--upload", action="store_true", help="Archive nach dem Bauen mit gh hochladen (fragt vorher)")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip", nargs="*", default=[], choices=["x64", "arm64", "linux"], help="Pakete auslassen")
    args = parser.parse_args()
    if sys.platform != "win32" and "x64" not in args.skip:
        raise SystemExit("Das x64-Paket (PyInstaller) lässt sich nur unter Windows bauen; mit --skip x64 überspringen.")
    if not args.skip_tests:
        run(sys.executable, "-m", "pytest", "-q")
    steps = {"x64": "package_windows.py", "arm64": "package_windows_arm64.py", "linux": "package_linux.py"}
    for key, script in steps.items():
        if key not in args.skip:
            run(sys.executable, ROOT / "scripts" / script)
    run(sys.executable, ROOT / "scripts" / "verify_release.py")
    files = [ROOT / "dist" / name for name in ARCHIVES if (ROOT / "dist" / name).is_file()]
    print("\nFertig. Archive in dist/:")
    for path in files:
        print(f"  {path.name}  {path.stat().st_size / 1_000_000:.0f} MB")
    if not args.upload:
        print("\nZum Hochladen erneut mit --upload starten.")
        return
    if not shutil.which("gh"):
        raise SystemExit("gh (GitHub CLI) nicht gefunden.")
    if input(f"{len(files)} Dateien zum Release {args.tag} hochladen (vorhandene werden ersetzt)? [j/N] ").strip().lower() != "j":
        raise SystemExit("Abgebrochen.")
    run("gh", "release", "upload", args.tag, *files, "--clobber")


if __name__ == "__main__":
    main()
