"""Fetch the standard LGPLv3 / incorporated GPLv3 texts from GNU."""
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


def main():
    folder = ROOT / "licenses"
    folder.mkdir(exist_ok=True)
    for name, url in [("LGPL-3.0.txt", "https://raw.githubusercontent.com/qt/qtbase/dev/LICENSES/LGPL-3.0-only.txt"),
                      ("GPL-3.0.txt", "https://raw.githubusercontent.com/qt/qtbase/dev/LICENSES/GPL-3.0-only.txt")]:
        target = folder / name
        if not target.exists():
            with urllib.request.urlopen(url, timeout=30) as response:
                target.write_bytes(response.read())


if __name__ == "__main__":
    main()
