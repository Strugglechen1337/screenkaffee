"""Batch helpers for the workspace: export with size/effects, lossless recompression, auto-renaming."""
from pathlib import Path
import re
from PIL import Image, PngImagePlugin
from .color import save_image
from .effects import apply_effects

EXPORT_FORMATS = (".png", ".jpg", ".jxl", ".webp", ".tiff", ".bmp", ".gif")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".jxl", ".webp", ".tif", ".tiff", ".bmp", ".gif")


def safe_name(title, fallback="Aufnahme"):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", str(title)).strip(" .")
    return name[:120] or fallback


def unique_path(folder, stem, suffix):
    folder = Path(folder)
    candidate = folder / (stem + suffix)
    number = 2
    while candidate.exists():
        candidate = folder / f"{stem}_{number}{suffix}"
        number += 1
    return candidate


def split_title_extension(title):
    """("Name.jpg") -> ("Name", ".jpg") when the typed title ends in a known image extension."""
    path = Path(title)
    if path.suffix.lower() in IMAGE_EXTENSIONS and path.stem:
        return path.stem, (".jpg" if path.suffix.lower() == ".jpeg" else ".tif" if path.suffix.lower() == ".tif" else path.suffix.lower())
    return title, None


def export_any(image, title, path, quality=95):
    """Save one image as a picture, or as a one-page PDF / Word document when the path ends in .pdf / .docx."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".pdf", ".docx"):
        from .documents import export_pdf, export_docx
        (export_pdf if suffix == ".pdf" else export_docx)([(title, image, "")], path, title)
        return path
    save_image(image, path, quality=quality, metadata={"Software": "screenkaffee"})
    return path


def export_batch(items, folder, suffix=".png", effects=None, quality=90):
    """items: iterable of (title, PIL image). Returns the written paths; existing files are never overwritten."""
    if suffix not in EXPORT_FORMATS:
        raise ValueError("Unterstützte Exportformate: PNG, JPEG, JPEG XL, WebP, TIFF, BMP, GIF.")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for title, image in items:
        result = apply_effects(image, effects) if effects else image
        path = unique_path(folder, safe_name(title), suffix)
        save_image(result, path, quality=quality, metadata={"Software": "screenkaffee"})
        written.append(path)
    return written


def renumber_titles(rows, prefix="screenshot", start=1):
    """New titles "<prefix>_1", "<prefix>_2" … in capture order (oldest first); returns [(id, title)]."""
    prefix = safe_name(prefix, "screenshot")
    ordered = sorted(rows, key=lambda row: (row["created"], row["id"]))
    return [(row["id"], f"{prefix}_{start + index}") for index, row in enumerate(ordered)]


def recompress_png(path):
    """Lossless re-encode (maximum deflate, optimised tables). Keeps colour profile and text chunks.
    Returns the number of bytes saved; the file is only replaced when it gets smaller."""
    path = Path(path)
    before = path.stat().st_size
    with Image.open(path) as source:
        source.load()
        image = source.copy()
        profile = source.info.get("icc_profile")
        text = dict(getattr(source, "text", {}) or {})
    info = PngImagePlugin.PngInfo()
    for key, value in text.items():
        info.add_text(key, value)
    temporary = path.with_name(path.name + ".tmp")
    try:
        image.save(temporary, format="PNG", optimize=True, compress_level=9, icc_profile=profile, pnginfo=info)
        if temporary.stat().st_size < before:
            saved = before - temporary.stat().st_size
            temporary.replace(path)
            return saved
        return 0
    finally:
        temporary.unlink(missing_ok=True)
