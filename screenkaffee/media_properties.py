from .i18n import tr
from pathlib import Path
import json
import mimetypes
from PIL import Image, ImageCms


def media_properties(path, kind="image", metadata=None):
    path = Path(path)
    stat = path.stat()
    values = {tr("Datei"): str(path.resolve()), tr("Dateigröße (Byte)"): stat.st_size,
              tr("Medientyp"): kind, tr("MIME-Typ"): mimetypes.guess_type(path.name)[0],
              tr("Geändert (Unixzeit)"): stat.st_mtime, tr("Aufnahmemetadaten"): metadata or {}}
    if kind == "image":
        with Image.open(path) as image:
            values.update({tr("Breite"): image.width, tr("Höhe"): image.height, tr("Format"): image.format,
                           tr("Farbmodus"): image.mode, tr("Bildanzahl"): getattr(image, "n_frames", 1), tr("DPI"): image.info.get("dpi")})
            if image.info.get("icc_profile"):
                import io
                profile = ImageCms.ImageCmsProfile(io.BytesIO(image.info["icc_profile"]))
                values[tr("Farbprofil")] = ImageCms.getProfileName(profile).strip()
    elif kind == "video":
        from .media import probe_media
        info = probe_media(path)
        values.update({tr("Breite"): info.width, tr("Höhe"): info.height, tr("Dauer (Sekunden)"): info.duration,
                       tr("Bilder pro Sekunde"): info.fps, tr("Ton vorhanden"): info.has_audio, tr("Videocodec"): info.codec})
        try:
            values.update(container_details(path))
        except Exception:
            pass  # basic video info above is still valid without the extended details
    return values


def parse_ffmpeg_info(text):
    """Extract container, bitrate and audio stream details from `ffmpeg -i` output."""
    import re
    values = {}
    container = re.search(r"Input #0, ([^,]+(?:,[^,]+)*?), from", text)
    if container:
        values[tr("Container")] = container.group(1).strip()
    bitrate = re.search(r"Duration:[^\n]*bitrate:\s*(\d+) kb/s", text)
    if bitrate:
        values[tr("Gesamtbitrate (kbit/s)")] = int(bitrate.group(1))
    audio = re.search(r"Stream #\d+:\d+[^\n]*?: Audio: (\w+)[^\n]*?, (\d+) Hz, ([^,\n]+)(?:,[^\n]*?(\d+) kb/s)?", text)
    if audio:
        values.update({tr("Audiocodec"): audio.group(1), tr("Abtastrate (Hz)"): int(audio.group(2)), tr("Audiokanäle"): audio.group(3).strip()})
        if audio.group(4):
            values[tr("Audiobitrate (kbit/s)")] = int(audio.group(4))
    return values


def container_details(path):
    from .media import ffmpeg_binary, run
    result = run([ffmpeg_binary(), "-hide_banner", "-i", str(Path(path).resolve())], timeout=30)
    return parse_ffmpeg_info(result.stderr.decode(errors="replace"))
