"""Explicit scRGB (linear Rec.709, 1 = 80 nit) to tagged sRGB conversion.

SDR white is a DISPLAYCONFIG_SDR_WHITE_LEVEL measurement, never inferred from
the brightest pixel. No exposure adaptation: identical pixels remain identical
across captures. The shoulder is an SDR rendering choice, not HDR preservation.
"""
import numpy as np
from pathlib import Path
from PIL import Image, ImageCms, PngImagePlugin
from .i18n import tr

try:                                            # registers the JPEG XL reader/writer with Pillow
    import pillow_jxl  # noqa: F401
    JXL_AVAILABLE = True
except ImportError:
    JXL_AVAILABLE = False


_F16_LUTS = {}


def _f16_neutral_lut(white_nits: float) -> np.ndarray:
    key = float(white_nits)
    lut = _F16_LUTS.get(key)
    if lut is None:
        vals = np.arange(65536, dtype=np.uint16).view(np.float16).astype(np.float32)
        vals = np.nan_to_num(vals, nan=0, posinf=125, neginf=0)
        vals = np.maximum(vals / (key / 80), 0)
        lut = np.rint(np.clip(linear_to_srgb(np.clip(vals, 0, 1)), 0, 1) * 255).astype(np.uint8)
        _F16_LUTS[key] = lut
    return lut


def linear_to_srgb(value):
    value = np.maximum(np.asarray(value, dtype=np.float32), 0)
    return np.where(value <= 0.0031308, 12.92 * value,
                    1.055 * np.power(value, 1 / 2.4) - 0.055)


def scrgb_to_srgb(rgb, white_nits, mode="neutral"):
    if not np.isfinite(white_nits) or not 40 <= white_nits <= 1000:
        raise ValueError(tr("Ungültiger SDR-Weißpegel (40–1000 cd/m² erforderlich)."))
    if isinstance(rgb, np.ndarray) and rgb.dtype == np.float16 and rgb.ndim == 3 and rgb.shape[-1] == 3:
        if mode == "neutral":
            return _f16_neutral_lut(white_nits)[rgb.view(np.uint16)]
        if mode == "highlights":
            out = _f16_neutral_lut(white_nits)[rgb.view(np.uint16)]
            threshold = 0.8 * (white_nits / 80)
            peak16 = np.max(rgb, axis=-1)
            mask = ~(peak16 <= threshold)
            if np.any(mask):
                out[mask] = scrgb_to_srgb(rgb[mask].reshape(-1, 1, 3).astype(np.float32), white_nits, "highlights")[:, 0]
            return out
    rgb = np.asarray(rgb, dtype=np.float32)
    if rgb.ndim != 3 or rgb.shape[-1] != 3:
        raise ValueError(tr("RGB-Array mit drei Kanälen erforderlich."))
    rgb = np.nan_to_num(rgb, nan=0, posinf=125, neginf=0)
    rgb = np.maximum(rgb / (white_nits / 80), 0)
    if mode == "highlights":
        # A hue-preserving shoulder above 0.8 linear; fixed, content-independent.
        peak = np.max(rgb, axis=-1, keepdims=True)
        mapped = np.where(peak > .8, .8 + .2 * (1 - np.exp(-np.maximum(peak - .8, 0) / .2)), peak)
        rgb *= mapped / np.maximum(peak, 1e-8)
    elif mode != "neutral":
        raise ValueError(tr("Unbekanntes Tone-Mapping."))
    # Neutral deliberately preserves SDR pixels exactly, clipping HDR highlights.
    return np.rint(np.clip(linear_to_srgb(np.clip(rgb, 0, 1)), 0, 1) * 255).astype(np.uint8)


def srgb_profile():
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def save_png(image: Image.Image, path, metadata=None):
    info = PngImagePlugin.PngInfo()
    for key, value in (metadata or {}).items():
        info.add_text(str(key), str(value))
    image.convert("RGBA" if "A" in image.getbands() else "RGB").save(path, format="PNG", icc_profile=srgb_profile(), pnginfo=info)


def import_srgb(path):
    """Normalize profiled external images once before editor operations."""
    with Image.open(path) as source:
        source.load()
        alpha = source.convert("RGBA").getchannel("A") if "A" in source.getbands() or "transparency" in source.info else None
        if source.info.get("icc_profile"):
            import io
            profile = ImageCms.ImageCmsProfile(io.BytesIO(source.info["icc_profile"]))
            working = source if source.mode in ("RGB", "RGBA", "CMYK", "LAB", "L") else source.convert("RGB")
            result = ImageCms.profileToProfile(working, profile, ImageCms.createProfile("sRGB"), outputMode="RGB")
        else:
            result = source.convert("RGB")
        if alpha is not None:
            result.putalpha(alpha)
        return result


def save_image(image, path, quality=95, metadata=None):
    suffix = Path(path).suffix.lower()
    if suffix == ".png":
        return save_png(image, path, metadata)
    formats = {".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP", ".tif": "TIFF", ".tiff": "TIFF", ".bmp": "BMP", ".gif": "GIF"}
    if suffix == ".jxl":
        if not JXL_AVAILABLE:
            raise ValueError(tr("JPEG XL braucht das Paket pillow-jxl-plugin."))
        formats[".jxl"] = "JXL"
    if suffix not in formats:
        raise ValueError(tr("Unterstützte Bildformate: PNG, JPEG, JPEG XL, WebP, TIFF, BMP und GIF."))
    output = image
    if suffix in (".jpg", ".jpeg", ".bmp"):
        if "A" in image.getbands():
            background = Image.new("RGBA", image.size, "white")
            output = Image.alpha_composite(background, image.convert("RGBA")).convert("RGB")
        else:
            output = image.convert("RGB")
    options = {"icc_profile": srgb_profile()} if suffix in (".jpg", ".jpeg", ".webp", ".tif", ".tiff") else {}
    if suffix in (".jpg", ".jpeg", ".webp", ".jxl"):
        options["quality"] = max(1, min(100, int(quality)))
    output.save(path, format=formats[suffix], **options)

