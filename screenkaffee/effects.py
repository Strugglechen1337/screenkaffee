"""Deterministic, non-destructive image finishing operations."""
from dataclasses import asdict, dataclass, fields
import math
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps


@dataclass
class ImageEffects:
    scale_percent: int = 100
    rotation: int = 0
    flip_horizontal: bool = False
    flip_vertical: bool = False
    grayscale: bool = False
    brightness: float = 1.0
    contrast: float = 1.0
    padding: int = 0
    background: str = "#ffffff"
    transparent_background: bool = False
    auto_canvas: bool = False
    corner_radius: int = 0
    border_width: int = 0
    border_color: str = "#1e293b"
    shadow_radius: int = 0
    shadow_offset: int = 8
    shadow_opacity: int = 100
    torn_edge: int = 0
    reflection_percent: int = 0
    watermark: str = ""
    watermark_opacity: int = 100
    watermark_position: str = "bottom-right"
    watermark_image: str = ""
    watermark_scale: int = 20

    @classmethod
    def from_dict(cls, data):
        keys = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in keys})

    def to_dict(self):
        return asdict(self)


def watermark_alignment(position):
    from PySide6.QtCore import Qt
    vertical, _, horizontal = position.partition("-")
    v = {"top": Qt.AlignmentFlag.AlignTop, "middle": Qt.AlignmentFlag.AlignVCenter}.get(vertical, Qt.AlignmentFlag.AlignBottom)
    h = {"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignHCenter}.get(horizontal, Qt.AlignmentFlag.AlignRight)
    return v | h


def paste_watermark_image(rgba, e, margin=12):
    with Image.open(e.watermark_image) as source:
        mark = source.convert("RGBA")
    width = max(1, round(rgba.width * max(1, min(100, e.watermark_scale)) / 100))
    mark = mark.resize((width, max(1, round(mark.height * width / mark.width))), Image.Resampling.LANCZOS)
    mark.putalpha(mark.getchannel("A").point(lambda a: a * max(0, min(255, e.watermark_opacity)) // 255))
    vertical, _, horizontal = e.watermark_position.partition("-")
    x = {"left": margin, "center": (rgba.width - mark.width) // 2}.get(horizontal, rgba.width - mark.width - margin)
    y = {"top": margin, "middle": (rgba.height - mark.height) // 2}.get(vertical, rgba.height - mark.height - margin)
    layer = Image.new("RGBA", rgba.size)
    layer.paste(mark, (x, y))
    return Image.alpha_composite(rgba, layer)


def adjust_colors(image, e):
    """Grayscale, brightness and contrast (shared by export and the editor's live preview)."""
    rgba = image.convert("RGBA")
    if e.grayscale:
        alpha = rgba.getchannel("A")
        rgba = ImageOps.grayscale(rgba).convert("RGBA")
        rgba.putalpha(alpha)
    for enhancer, value in [(ImageEnhance.Brightness, e.brightness), (ImageEnhance.Contrast, e.contrast)]:
        if value != 1:
            alpha = rgba.getchannel("A")
            rgba = enhancer(rgba.convert("RGB")).enhance(value).convert("RGBA")
            rgba.putalpha(alpha)
    return rgba


def apply_effects(image, options):
    e = ImageEffects.from_dict(options) if isinstance(options, dict) else options
    if not 5 <= e.scale_percent <= 400:
        raise ValueError("Bildgröße muss zwischen 5 und 400 Prozent liegen.")
    rgba = image.convert("RGBA")
    rgba = adjust_colors(rgba, e)
    if e.flip_horizontal:
        rgba = ImageOps.mirror(rgba)
    if e.flip_vertical:
        rgba = ImageOps.flip(rgba)
    if e.rotation % 360:
        rgba = rgba.rotate(-e.rotation, expand=True, resample=Image.Resampling.BICUBIC)
    if e.scale_percent != 100:
        size = tuple(max(1, round(n * e.scale_percent / 100)) for n in rgba.size)
        if size[0] * size[1] > 150_000_000:
            raise ValueError("Das Ergebnis überschreitet 150 Megapixel.")
        rgba = rgba.resize(size, Image.Resampling.LANCZOS)
    width, height = rgba.size
    mask = Image.new("L", rgba.size, 255)
    if e.corner_radius:
        mask = Image.new("L", rgba.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1),
            radius=max(0, min(e.corner_radius, width // 2, height // 2)), fill=255)
    if e.torn_edge:
        amplitude = min(max(0, e.torn_edge), min(width, height) // 4)
        torn = Image.new("L", rgba.size, 0)
        top = [(x, amplitude * (.55 + .3 * math.sin(x * .17) + .15 * math.cos(x * .47))) for x in range(0, width + 5, 4)]
        bottom = [(x, height - 1 - amplitude * (.55 + .3 * math.sin(x * .19) + .15 * math.cos(x * .39))) for x in range(width, -5, -4)]
        ImageDraw.Draw(torn).polygon(top + bottom, fill=255)
        mask = ImageChops.multiply(mask, torn)
    rgba.putalpha(ImageChops.multiply(rgba.getchannel("A"), mask))
    if e.border_width:
        border = Image.new("RGBA", rgba.size)
        ImageDraw.Draw(border).rounded_rectangle((0, 0, width - 1, height - 1),
            radius=max(0, e.corner_radius), outline=e.border_color, width=min(max(e.border_width, 1), 100))
        border.putalpha(ImageChops.multiply(border.getchannel("A"), mask))
        rgba = Image.alpha_composite(rgba, border)
    if e.watermark_image:
        rgba = paste_watermark_image(rgba, e)
    if e.watermark:
        from PySide6.QtCore import Qt, QRectF
        from PySide6.QtGui import QImage, QPainter, QColor, QFont
        from .editor import pil_to_qimage, qimage_to_pil
        layer = pil_to_qimage(rgba)
        painter = QPainter(layer)
        font = QFont("Sans Serif")
        font.setPixelSize(max(12, min(width, height) // 24))
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, max(0, min(255, e.watermark_opacity))))
        painter.drawText(QRectF(12, 12, width - 24, height - 24), watermark_alignment(e.watermark_position), e.watermark)
        painter.end()
        rgba = qimage_to_pil(layer, keep_alpha=True)
    reflection_height = min(height, round(height * max(0, min(100, e.reflection_percent)) / 100))
    shadow_margin = min(200, max(0, e.shadow_radius)) * 2 + (abs(e.shadow_offset) if e.shadow_radius else 0)
    pad = max(0, min(1000, e.padding)) + shadow_margin
    size = (width + pad * 2, height + pad * 2 + reflection_height)
    if size[0] * size[1] > 150_000_000:
        raise ValueError("Das Ergebnis überschreitet 150 Megapixel.")
    background = (0, 0, 0, 0) if e.transparent_background else e.background
    canvas = Image.new("RGBA", size, background)
    if e.shadow_radius:
        shadow = Image.new("RGBA", size)
        alpha = Image.new("L", size, 0)
        alpha.paste(rgba.getchannel("A"), (pad + e.shadow_offset, pad + e.shadow_offset))
        alpha = alpha.filter(ImageFilter.GaussianBlur(min(200, e.shadow_radius)))
        alpha = alpha.point(lambda a: round(a * max(0, min(255, e.shadow_opacity)) / 255))
        shadow.putalpha(alpha)
        canvas = Image.alpha_composite(canvas, shadow)
    canvas.alpha_composite(rgba, (pad, pad))
    if reflection_height:
        reflected = ImageOps.flip(rgba).crop((0, 0, width, reflection_height))
        fade = Image.new("L", (1, reflection_height))
        fade.putdata([round(100 * (1 - y / reflection_height)) for y in range(reflection_height)])
        reflected.putalpha(ImageChops.multiply(reflected.getchannel("A"), fade.resize(reflected.size)))
        canvas.alpha_composite(reflected, (pad, pad + height))
    return canvas if e.transparent_background else canvas.convert("RGB")


def cut_out(image, axis, start, end, gap=0, torn=0):
    """Remove the strip [start, end) along axis ('y' = horizontal strip, 'x' = vertical) and join the rest."""
    size = image.height if axis == "y" else image.width
    start, end = max(0, int(start)), min(size, int(end))
    gap, torn = max(0, min(200, int(gap))), max(0, min(100, int(torn)))
    if end <= start:
        raise ValueError("Der Bereich ist leer.")
    if end - start >= size:
        raise ValueError("Es muss mindestens ein Teil des Bildes übrig bleiben.")
    new_span = size - (end - start) + gap
    mode = "RGBA" if (gap or torn) and image.mode == "RGBA" else image.mode
    fill = (0, 0, 0, 0) if mode == "RGBA" else (255, 255, 255)
    result = Image.new(mode, (image.width, new_span) if axis == "y" else (new_span, image.height), fill)
    if axis == "y":
        first = image.crop((0, 0, image.width, start))
        second = image.crop((0, end, image.width, image.height))
        result.paste(first, (0, 0))
        result.paste(second, (0, start + gap))
        if torn:
            draw = ImageDraw.Draw(result)
            points = [(x, start + gap / 2 + round(torn * math.sin(x * .25))) for x in range(0, image.width + 4, 4)]
            draw.line(points, fill=fill, width=max(2, gap or torn))
    else:
        first = image.crop((0, 0, start, image.height))
        second = image.crop((end, 0, image.width, image.height))
        result.paste(first, (0, 0))
        result.paste(second, (start + gap, 0))
        if torn:
            draw = ImageDraw.Draw(result)
            points = [(start + gap / 2 + round(torn * math.sin(y * .25)), y) for y in range(0, image.height + 4, 4)]
            draw.line(points, fill=fill, width=max(2, gap or torn))
    return result
