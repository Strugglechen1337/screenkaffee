"""One-time source rename, preserving already generated artifacts and data."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    old, new = ROOT / "prismcapture", ROOT / "screenkaffee"
    if new.exists():
        raise RuntimeError("screenkaffee-Quellverzeichnis existiert bereits.")
    assert old.resolve().parent == ROOT and new.resolve().parent == ROOT
    files = [path for folder in [old, ROOT / "scripts", ROOT / "tests"]
             for path in folder.rglob("*") if path.suffix in (".py", ".ps1") and path.name != Path(__file__).name]
    files += [path for path in ROOT.iterdir() if path.is_file() and path.suffix in (".py", ".md", ".toml", ".cmd")]
    for path in files:
        text = path.read_text(encoding="utf-8")
        updated = text.replace("PRISMCAPTURE", "SCREENKAFFEE").replace("PrismCapture", "screenkaffee").replace("prismcapture", "screenkaffee")
        if updated != text:
            path.write_text(updated, encoding="utf-8")
    old.rename(new)
    (ROOT / "Start-PrismCapture.cmd").rename(ROOT / "Start-screenkaffee.cmd")


if __name__ == "__main__":
    main()
