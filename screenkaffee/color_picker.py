"""Explicit sRGB color values for the standalone desktop picker."""
import colorsys
import re


def color_formats(value):
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError("Ungültiger RGB-Farbwert.")
    r, g, b = (int(value[i:i + 2], 16) for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    hv, sv, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return {"HEX": value.upper(), "RGB": f"rgb({r}, {g}, {b})",
            "HSL": f"hsl({round(h * 360)}, {round(s * 100)}%, {round(l * 100)}%)",
            "HSV": f"hsv({round(hv * 360)}, {round(sv * 100)}%, {round(v * 100)}%)"}
