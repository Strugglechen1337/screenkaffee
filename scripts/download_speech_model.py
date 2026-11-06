"""Download only CTranslate2 weights/config/tokenizer, never repository code."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


def main():
    repository = "Systran/faster-whisper-base"
    target = ROOT / "models" / "whisper-base"
    target.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen("https://huggingface.co/api/models/" + repository, timeout=30) as response:
        info = json.load(response)
    revision = info["sha"]
    available = {f["rfilename"] for f in info["siblings"]}
    manifest = {"repository": repository, "revision": revision, "assets": []}
    for name in ["config.json", "model.bin", "tokenizer.json", "vocabulary.json", "vocabulary.txt", "README.md"]:
        if name not in available:
            continue
        path = target / name
        url = f"https://huggingface.co/{repository}/resolve/{revision}/{name}"
        if not path.exists():
            print("Downloading speech model: " + name, flush=True)
            with urllib.request.urlopen(url, timeout=90) as response, path.open("wb") as out:
                while data := response.read(1024 * 1024):
                    out.write(data)
        manifest["assets"].append({"name": name, "url": url, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
