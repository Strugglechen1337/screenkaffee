"""Named tool presets (color, width, font size, tool) stored next to the app."""
import json
from pathlib import Path
from .paths import application_root

KEYS = ("tool", "color", "width", "font_size")


def preset_file():
    from .workspace import portable_root
    return portable_root() / "tool_presets.json"


STYLE_KEYS = ("color", "width", "font_size", "font_family", "bold", "italic", "underline", "align", "text_color",
              "fill", "opacity", "radius", "arrow_style", "line_style", "curve", "zoom", "blur",
              "glow_radius", "glow_color", "shadow_radius", "shadow_offset", "shadow_color", "connect", "step_format")


def defaults_file():
    return preset_file().with_name("tool_defaults.json")


def load_defaults(path=None):
    try:
        data = json.loads(Path(path or defaults_file()).read_text(encoding="utf-8"))
        return {name: {k: v for k, v in values.items() if k in STYLE_KEYS}
                for name, values in data.items() if isinstance(values, dict)}
    except (OSError, ValueError, AttributeError):
        return {}


def save_defaults(values, path=None):
    path = Path(path or defaults_file())
    path.parent.mkdir(parents=True, exist_ok=True)
    content = {name: {k: v for k, v in style.items() if k in STYLE_KEYS} for name, style in values.items()}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_presets(path=None):
    path = Path(path) if path else preset_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): {x: v[x] for x in KEYS if x in v} for k, v in data.items() if isinstance(v, dict)}


def save_preset(name, values, path=None):
    name = name.strip()
    if not name:
        raise ValueError("Name fehlt")
    path = Path(path) if path else preset_file()
    presets = load_presets(path)
    presets[name] = {k: values[k] for k in KEYS if k in values}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(presets, ensure_ascii=False, indent=2), encoding="utf-8")
    return presets


def delete_preset(name, path=None):
    path = Path(path) if path else preset_file()
    presets = load_presets(path)
    presets.pop(name, None)
    path.write_text(json.dumps(presets, ensure_ascii=False, indent=2), encoding="utf-8")
    return presets
