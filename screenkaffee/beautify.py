"""Schicker Screenshot: Farbverlauf-Hintergrund, abgerundete Ecken, weicher Schatten und optional ein Fensterrahmen."""
from dataclasses import dataclass, asdict

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

STYLES = [("coffee", "Kaffee", ("#3b2416", "#c8875a")), ("sunset", "Sonnenuntergang", ("#ff9a8b", "#ff6a88")),
          ("ocean", "Ozean", ("#2e3192", "#1bc6e8")), ("mint", "Minze", ("#d4fc79", "#96e6a1")),
          ("lavender", "Lavendel", ("#a18cd1", "#fbc2eb")), ("night", "Nacht", ("#141e30", "#3a4a63")),
          ("light", "Schlicht hell", ("#f5f5f7", "#dcdce3"))]
FRAMES = [("none", "Ohne Fensterrahmen"), ("mac", "Fenster hell (drei Punkte)"), ("mac-dark", "Fenster dunkel (drei Punkte)"),
          ("windows", "Fenster im Windows-Stil")]
ASPECTS = [("free", "Passend zum Bild", None), ("16:9", "16:9 (Präsentation, YouTube)", 16 / 9),
           ("4:3", "4:3", 4 / 3), ("1:1", "1:1 (quadratisch)", 1.0), ("9:16", "9:16 (Hochformat, Story)", 9 / 16)]
LIGHTS = ("#ff5f57", "#febc2e", "#28c840")


@dataclass
class Options:
    style: str = "coffee"
    frame: str = "none"
    title: str = ""
    padding: int = -1          # -1 = automatisch (etwa 7 % der Bildgröße)
    radius: int = 14
    shadow: bool = True
    aspect: str = "free"

    @classmethod
    def from_dict(cls, data):
        known = {key: value for key, value in (data or {}).items() if key in cls.__dataclass_fields__}
        options = cls(**known)
        if options.style not in {s for s, _, _ in STYLES}:
            options.style = "coffee"
        if options.frame not in {f for f, _ in FRAMES}:
            options.frame = "none"
        if options.aspect not in {a for a, _, _ in ASPECTS}:
            options.aspect = "free"
        return options

    def to_dict(self):
        return asdict(self)


def gradient(size, colors, angle=135):
    """Linearer Farbverlauf (Winkel in Grad, 135 = von oben links nach unten rechts)."""
    width, height = size
    start, end = (np.array(Image.new("RGB", (1, 1), color).getpixel((0, 0)), dtype=np.float32) for color in colors)
    rad = np.deg2rad(angle - 90)
    x = np.linspace(0, 1, width, dtype=np.float32)[None, :]
    y = np.linspace(0, 1, height, dtype=np.float32)[:, None]
    t = x * np.cos(rad) * width + y * np.sin(rad) * height
    t = (t - t.min()) / max(1e-6, float(t.max() - t.min()))
    pixels = start + (end - start) * t[..., None]
    return Image.fromarray(pixels.clip(0, 255).astype(np.uint8), "RGB").convert("RGBA")


def rounded(image, radius):
    """RGBA-Bild mit abgerundeten Ecken (kantengeglättet über 4-fache Maske)."""
    card = image.convert("RGBA")
    radius = max(0, min(int(radius), min(card.size) // 2))
    if radius == 0:
        return card
    big = Image.new("L", (card.width * 4, card.height * 4), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, big.width - 1, big.height - 1), radius * 4, fill=255)
    mask = big.resize(card.size, Image.Resampling.LANCZOS)
    alpha = Image.fromarray(np.minimum(np.asarray(card.getchannel("A")), np.asarray(mask)), "L")
    card.putalpha(alpha)
    return card


def font(size):
    for name in ("segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def add_frame(image, kind, title=""):
    """Fensterleiste über das Bild setzen. Die Leistenhöhe wächst mit der Bildbreite."""
    if kind == "none":
        return image.convert("RGB")
    bar = max(28, round(image.width * 0.028))
    dark = kind == "mac-dark"
    background = "#2b2b2e" if dark else ("#ffffff" if kind == "windows" else "#ececee")
    framed = Image.new("RGB", (image.width, image.height + bar), background)
    framed.paste(image.convert("RGB"), (0, bar))
    draw = ImageDraw.Draw(framed)
    draw.line((0, bar - 1, image.width, bar - 1), fill="#1c1c1e" if dark else "#d6d6d8")
    dot = bar * 0.42
    if kind in ("mac", "mac-dark"):
        for index, color in enumerate(LIGHTS):
            cx, cy = bar * 0.62 + index * dot * 1.65, bar / 2
            draw.ellipse((cx - dot / 2, cy - dot / 2, cx + dot / 2, cy + dot / 2), fill=color)
    else:
        unit, color = bar * 0.28, "#333333"
        for index in range(3):
            cx, cy = image.width - bar * (0.8 + 1.5 * index), bar / 2
            if index == 0:
                draw.line((cx - unit / 2, cy - unit / 2, cx + unit / 2, cy + unit / 2), fill=color, width=max(1, bar // 20))
                draw.line((cx - unit / 2, cy + unit / 2, cx + unit / 2, cy - unit / 2), fill=color, width=max(1, bar // 20))
            elif index == 1:
                draw.rectangle((cx - unit / 2, cy - unit / 2, cx + unit / 2, cy + unit / 2), outline=color, width=max(1, bar // 24))
            else:
                draw.line((cx - unit / 2, cy, cx + unit / 2, cy), fill=color, width=max(1, bar // 24))
    if title:
        text_font = font(max(10, round(bar * 0.42)))
        width = draw.textlength(title, font=text_font)
        x = bar * 0.5 if kind == "windows" else (image.width - width) / 2
        draw.text((x, bar / 2), title, fill="#d0d0d4" if dark else "#4a4a4e", font=text_font, anchor="lm")
    return framed


def beautify(image, options=None, **overrides):
    """Fertiges Bild (RGB) im gewählten Stil."""
    options = Options.from_dict({**(options.to_dict() if isinstance(options, Options) else options or {}), **overrides})
    content = add_frame(image, options.frame, options.title)
    card = rounded(content, options.radius)
    pad = options.padding if options.padding >= 0 else max(24, round(max(card.size) * 0.07))
    width, height = card.width + 2 * pad, card.height + 2 * pad
    ratio = next((r for key, _, r in ASPECTS if key == options.aspect), None)
    if ratio:
        if width / height < ratio:
            width = round(height * ratio)
        else:
            height = round(width / ratio)
    colors = next(c for key, _, c in STYLES if key == options.style)
    canvas = gradient((width, height), colors)
    x, y = (width - card.width) // 2, (height - card.height) // 2
    if options.shadow and pad > 0:
        blur = max(4, min(pad // 2, round(max(card.size) * 0.025)))
        shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        alpha = card.getchannel("A").point(lambda value: value * 0.45)
        shadow.paste(Image.new("RGBA", card.size, (0, 0, 0, 255)), (x, y + blur // 2), alpha)
        canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(blur)))
    canvas.alpha_composite(card, (x, y))
    return canvas.convert("RGB")


def preview(image, options, longest=900):
    """Schnelle, verkleinerte Vorschau; Abstand und Ecken werden mitskaliert."""
    factor = min(1.0, longest / max(image.size))
    if factor >= 1:
        return beautify(image, options)
    small = image.resize((max(1, round(image.width * factor)), max(1, round(image.height * factor))), Image.Resampling.LANCZOS)
    padding = options.padding if options.padding < 0 else round(options.padding * factor)
    return beautify(small, options, padding=padding, radius=round(options.radius * factor))


def beautify_dialog(parent, image, settings=None, on_save=None, title=""):
    """Dialog mit Live-Vorschau; „Kopieren“ und „Als neue Aufnahme speichern“. settings: dict, wird aktualisiert."""
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import (QDialog, QHBoxLayout, QVBoxLayout, QFormLayout, QLabel, QComboBox, QSpinBox,
                                   QCheckBox, QLineEdit, QPushButton, QApplication)
    from .editor import pil_to_qimage
    settings = settings if settings is not None else {}
    options = Options.from_dict(settings.get("beautify"))
    options.title = title                     # der Titel gehört zur Aufnahme, nicht zu den gemerkten Einstellungen
    dialog = QDialog(parent)
    dialog.setWindowTitle("Schicker Screenshot")
    layout = QHBoxLayout(dialog)
    dialog.preview = QLabel()
    dialog.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
    dialog.preview.setMinimumSize(560, 400)
    layout.addWidget(dialog.preview, 1)
    side = QVBoxLayout()
    form = QFormLayout()
    dialog.style = QComboBox()
    for key, label, _ in STYLES:
        dialog.style.addItem(label, key)
    dialog.frame = QComboBox()
    for key, label in FRAMES:
        dialog.frame.addItem(label, key)
    dialog.title = QLineEdit(options.title)
    dialog.title.setPlaceholderText("Fenstertitel (optional)")
    dialog.aspect = QComboBox()
    for key, label, _ in ASPECTS:
        dialog.aspect.addItem(label, key)
    dialog.padding = QSpinBox()
    dialog.padding.setRange(-1, 400)
    dialog.padding.setSpecialValueText("automatisch")
    dialog.padding.setSuffix(" px")
    dialog.radius = QSpinBox()
    dialog.radius.setRange(0, 80)
    dialog.radius.setSuffix(" px")
    dialog.shadow = QCheckBox("Weicher Schatten")
    for combo, value in ((dialog.style, options.style), (dialog.frame, options.frame), (dialog.aspect, options.aspect)):
        combo.setCurrentIndex(max(0, combo.findData(value)))
    dialog.padding.setValue(options.padding)
    dialog.radius.setValue(options.radius)
    dialog.shadow.setChecked(options.shadow)
    form.addRow("Hintergrund", dialog.style)
    form.addRow("Rahmen", dialog.frame)
    form.addRow("Titel", dialog.title)
    form.addRow("Format", dialog.aspect)
    form.addRow("Abstand", dialog.padding)
    form.addRow("Ecken", dialog.radius)
    form.addRow(dialog.shadow)
    side.addLayout(form)
    dialog.size_label = QLabel()
    dialog.size_label.setObjectName("muted")
    side.addWidget(dialog.size_label)
    side.addStretch()
    copy = QPushButton("In Zwischenablage kopieren")
    save = QPushButton("Als neue Aufnahme speichern")
    save.setObjectName("primary")
    save.setEnabled(on_save is not None)
    close = QPushButton("Schließen")
    for button in (copy, save, close):
        side.addWidget(button)
    layout.addLayout(side)

    def current():
        return Options(dialog.style.currentData(), dialog.frame.currentData(), dialog.title.text().strip(),
                       dialog.padding.value(), dialog.radius.value(), dialog.shadow.isChecked(), dialog.aspect.currentData())

    def refresh():
        chosen = current()
        shown = preview(image, chosen)
        pix = QPixmap.fromImage(pil_to_qimage(shown))
        dpr = dialog.devicePixelRatioF() or 1
        box = dialog.preview.size()
        pix = pix.scaled(round(box.width() * dpr), round(box.height() * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        pix.setDevicePixelRatio(dpr)
        dialog.preview.setPixmap(pix)
        final = beautify_size(image, chosen)
        dialog.size_label.setText(f"Ergebnis: {final[0]} × {final[1]} px")
        settings["beautify"] = {key: value for key, value in chosen.to_dict().items() if key != "title"}

    def finished_image():
        return beautify(image, current())

    def do_copy():
        QApplication.clipboard().setImage(pil_to_qimage(finished_image()))
        copy.setText("Kopiert ✓")

    def do_save():
        on_save(finished_image())
        save.setText("Gespeichert ✓")

    timer = QTimer(dialog)
    timer.setSingleShot(True)
    timer.setInterval(120)
    timer.timeout.connect(refresh)
    for widget in (dialog.style, dialog.frame, dialog.aspect):
        widget.currentIndexChanged.connect(lambda _: timer.start())
    for widget in (dialog.padding, dialog.radius):
        widget.valueChanged.connect(lambda _: timer.start())
    dialog.title.textChanged.connect(lambda _: timer.start())
    dialog.shadow.toggled.connect(lambda _: timer.start())
    copy.clicked.connect(do_copy)
    save.clicked.connect(do_save)
    close.clicked.connect(dialog.accept)
    dialog.refresh, dialog.current, dialog.finished_image = refresh, current, finished_image
    dialog.copy_button, dialog.save_button = copy, save
    dialog.resize(1000, 620)
    refresh()
    return dialog


def beautify_size(image, options):
    """Endgröße ohne das Bild zu rendern."""
    bar = 0 if options.frame == "none" else max(28, round(image.width * 0.028))
    width, height = image.width, image.height + bar
    pad = options.padding if options.padding >= 0 else max(24, round(max(width, height) * 0.07))
    width, height = width + 2 * pad, height + 2 * pad
    ratio = next((r for key, _, r in ASPECTS if key == options.aspect), None)
    if ratio:
        if width / height < ratio:
            width = round(height * ratio)
        else:
            height = round(width / ratio)
    return width, height
