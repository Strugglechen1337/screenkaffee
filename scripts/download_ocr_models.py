"""Fetch data files from a pinned upstream commit; never execute repository code."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
COMMIT = "87416418657359cb625c412a48b6e1d6d41c29bd"


def main():
    target = ROOT / "models" / "tessdata"
    target.mkdir(parents=True, exist_ok=True)
    manifest = {"repository": "tesseract-ocr/tessdata_fast", "commit": COMMIT, "assets": []}
    for filename in ["deu.traineddata", "eng.traineddata", "LICENSE"]:
        path = target / filename
        url = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{COMMIT}/{filename}"
        if not path.exists():
            print("Downloading " + filename, flush=True)
            with urllib.request.urlopen(url, timeout=60) as source:
                content = source.read()
            if filename.endswith(".traineddata") and len(content) < 100_000:
                raise RuntimeError("Ungültige OCR-Sprachdatei.")
            path.write_bytes(content)
        manifest["assets"].append({"name": filename, "url": url, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
