"""Local video editing. All mutations render to a temporary file and validate
the result before replacing the requested output. Original inputs are untouched.
"""
from dataclasses import dataclass
import io
import json
import logging
import math
from pathlib import Path
import re
import subprocess
import uuid
from PIL import Image
from .integrations import tool, run
from .i18n import N_, tr

LOG = logging.getLogger(__name__)


class NoVideoStreamError(ValueError):
    """Kein Videostream: stabiler Fehlertyp für den Rückfall beim genauen Springen."""


def ffmpeg_binary():
    binary = tool("ffmpeg")
    if binary:
        return binary
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError) as exc:
        raise RuntimeError(tr("Die mitgelieferte FFmpeg-Laufzeit fehlt oder kann nicht gestartet werden.")) from exc


@dataclass
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    codec: str = ""


def probe_media(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(tr("Videodatei nicht gefunden."))
    ffprobe = tool("ffprobe")
    if ffprobe:
        result = run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace")[-2000:])
        data = json.loads(result.stdout)
        videos = [s for s in data["streams"] if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
        if not videos:
            raise NoVideoStreamError(tr("Datei enthält keinen Videostream."))
        video = videos[0]
        rate = video.get("avg_frame_rate", "0/1").split("/")
        fps = float(rate[0]) / max(1, float(rate[-1]))
        duration = float(video.get("duration") or data.get("format", {}).get("duration") or 0)
        return MediaInfo(duration, video["width"], video["height"], fps,
            any(s.get("codec_type") == "audio" for s in data["streams"]), video.get("codec_name", ""))
    result = run([ffmpeg_binary(), "-hide_banner", "-i", str(path), "-map", "0:v:0", "-frames:v", "1",
                  "-f", "image2pipe", "-vcodec", "png", "-"], timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace")[-2000:])
    text = result.stderr.decode(errors="replace")
    duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    fps = re.search(r"(\d+(?:\.\d+)?) fps", text)
    with Image.open(io.BytesIO(result.stdout)) as frame:
        width, height = frame.size
    seconds = sum(float(v) * multiplier for v, multiplier in zip(duration.groups(), [3600, 60, 1])) if duration else 0
    return MediaInfo(seconds, width, height, float(fps.group(1)) if fps else 30,
        bool(re.search(r"Stream[^\n]*Audio:", text)))


def extract_frame(path, seconds=0):
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(tr("Ungültige Bildposition."))
    result = run([ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-ss", str(seconds), "-i", str(Path(path).resolve()),
        "-map", "0:v:0", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"], timeout=60)
    if result.returncode or not result.stdout:
        raise RuntimeError(result.stderr.decode(errors="replace")[-2000:] or tr("An dieser Position ist kein Videobild verfügbar."))
    with Image.open(io.BytesIO(result.stdout)) as image:
        return image.convert("RGB")


def tempo_filters(speed):
    if not math.isfinite(speed) or not .125 <= speed <= 8:
        raise ValueError(tr("Geschwindigkeit muss zwischen 0,125 und 8 liegen."))
    remaining, factors = speed, []
    while remaining > 2:
        factors.append(2)
        remaining /= 2
    while remaining < .5:
        factors.append(.5)
        remaining /= .5
    factors.append(remaining)
    return ",".join(f"atempo={factor:.8g}" for factor in factors)


OUTPUT_FORMATS = (".mp4", ".mov", ".webm", ".avi", ".mkv", ".wmv", ".ogv", ".gif", ".apng", ".webp")
ANIMATED_FORMATS = (".gif", ".apng", ".webp")
QUALITIES = ("low", "medium", "high")
RESIZE_MODES = ("fitmax", "max", "exact")
VIDEO_SIZES = [(N_("Original"), None), ("1920 × 1080", (1920, 1080)), ("1280 × 720", (1280, 720)),
               ("854 × 480", (854, 480)), ("640 × 360", (640, 360)), ("320 × 180", (320, 180))]


def _even(value):
    return max(2, int(value) // 2 * 2)


def target_size(width, height, size, mode="fitmax"):
    """Output size for a target box. fitmax: scale to fit (up or down); max: never enlarge;
    exact: fit and pad with black to exactly the box. Aspect ratio is always kept."""
    if mode not in RESIZE_MODES:
        raise ValueError(tr("Unbekannter Skalierungsmodus."))
    box_width, box_height = size
    if min(box_width, box_height, width, height) < 1:
        raise ValueError(tr("Ungültige Größe."))
    scale = min(box_width / width, box_height / height)
    if mode == "max":
        scale = min(1, scale)
    new = (_even(width * scale), _even(height * scale))
    return (_even(box_width), _even(box_height)) if mode == "exact" else new


def _codec_options(suffix, quality="medium", loops=0):
    if quality not in QUALITIES:
        raise ValueError(tr("Unbekannte Qualitätsstufe."))
    level = QUALITIES.index(quality)
    if suffix == ".webm":
        return ["-c:v", "libvpx-vp9", "-crf", str((36, 30, 24)[level]), "-b:v", "0", "-c:a", "libopus"]
    if suffix == ".mkv":
        return ["-c:v", "libaom-av1", "-crf", str((40, 34, 28)[level]), "-b:v", "0", "-cpu-used", "8", "-row-mt", "1",
                "-pix_fmt", "yuv420p", "-c:a", "libopus"]
    if suffix == ".wmv":
        return ["-c:v", "wmv2", "-b:v", ("1M", "2M", "4M")[level], "-c:a", "wmav2"]
    if suffix == ".ogv":
        return ["-c:v", "libtheora", "-q:v", str((5, 7, 9)[level]), "-c:a", "libvorbis", "-q:a", str((3, 5, 7)[level])]
    if suffix == ".avi":
        return ["-c:v", "mpeg4", "-q:v", str((5, 3, 2)[level]), "-c:a", "mp3"]
    if suffix == ".apng":
        return ["-f", "apng", "-plays", str(int(loops))]
    if suffix == ".webp":
        return ["-c:v", "libwebp_anim", "-loop", str(int(loops)), "-q:v", str((50, 70, 90)[level])]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str((26, 20, 16)[level]), "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart"]


def _render(args, destination, inputs, timeout=3600):
    destination = Path(destination).resolve()
    if destination in [Path(p).resolve() for p in inputs]:
        raise ValueError(tr("Originalvideos werden nicht überschrieben. Bitte einen neuen Dateinamen verwenden."))
    if destination.suffix.lower() not in OUTPUT_FORMATS:
        raise ValueError(tr("Unterstützte Zielformate: MP4, MOV, WebM, AVI, MKV (AV1), WMV, OGV, GIF, animiertes PNG (.apng), animiertes WebP."))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".prism-{uuid.uuid4().hex}{destination.suffix}")
    try:
        result = run([ffmpeg_binary(), "-hide_banner", "-nostdin", "-loglevel", "error", *args, str(temporary)], timeout=timeout)
        if result.returncode or not temporary.exists() or temporary.stat().st_size == 0:
            raise RuntimeError(result.stderr.decode(errors="replace")[-4000:] or tr("Videokonvertierung erzeugte keine Datei."))
        if destination.suffix.lower() == ".webp":
            # Without ffprobe the bundled ffmpeg cannot size an animated WebP; check the RIFF header instead.
            head = temporary.read_bytes()[:12]
            if head[:4] != b"RIFF" or head[8:12] != b"WEBP":
                raise RuntimeError(tr("Videokonvertierung erzeugte kein gültiges WebP."))
        else:
            info = probe_media(temporary)
            if info.width < 1 or info.height < 1:
                raise RuntimeError(tr("Videokonvertierung erzeugte kein gültiges Bild."))
        temporary.replace(destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def kept_segments(start=0, end=None, cuts=None, duration=0, min_length=1e-3):
    """Verbleibende (Start, Ende)-Abschnitte in Sekunden nach dem Entfernen von `cuts` aus [start, end].

    Reste kürzer als `min_length` (beim Export: ein Videobild) werden mit den benachbarten Schnitten verschmolzen.
    Sie enthielten kein ganzes Bild, würden aber Ton behalten und Bild und Ton gegeneinander verschieben."""
    clip_end = float(end if end is not None else duration)
    start = float(start)
    if clip_end <= start:
        raise ValueError(tr("Ungültiger Endzeitpunkt."))
    if not cuts:
        return [(start, clip_end)]
    clamped = []
    for item in cuts:
        if item is None or len(item) != 2:
            raise ValueError(tr("Ungültiger Schnittbereich."))
        cs, ce = float(item[0]), float(item[1])
        if not (math.isfinite(cs) and math.isfinite(ce)) or cs < 0 or ce <= cs or (duration > 0 and ce > duration + .1):
            raise ValueError(tr("Ungültiger Schnittbereich."))
        cs, ce = max(start, cs), min(clip_end, ce)
        if ce > cs:
            clamped.append((cs, ce))
    min_length = max(1e-3, float(min_length))
    merged = []
    for cs, ce in sorted(clamped):
        if merged and cs - merged[-1][1] < min_length:
            merged[-1] = (merged[-1][0], max(merged[-1][1], ce))
        else:
            merged.append((cs, ce))
    segments = []
    cursor = start
    for cs, ce in merged:
        if cs - cursor >= min_length:
            segments.append((cursor, cs))
        cursor = max(cursor, ce)
    if clip_end - cursor >= min_length:
        segments.append((cursor, clip_end))
    if not segments:
        if min_length > 1e-3 and clamped:
            raise ValueError(tr("Die Schnitte entfernen das gesamte Video: Übrig bliebe weniger als ein Videobild. "
                             "Bitte einen Schnitt verkleinern."))
        raise ValueError(tr("Die Schnitte entfernen das gesamte Video."))
    return segments


def edit_video(source, destination, start=0, end=None, speed=1, mute=False, crop=None, width=None, volume=1,
               size=None, resize_mode="fitmax", quality="medium", fps=15, loops=0, cuts=None):
    info = probe_media(source)
    if not 1 <= int(fps) <= 60:
        raise ValueError(tr("Bilder pro Sekunde müssen zwischen 1 und 60 liegen."))
    if not 0 <= int(loops) <= 1000:
        raise ValueError(tr("Wiederholungen müssen zwischen 0 und 1000 liegen."))
    if not math.isfinite(start) or start < 0 or (info.duration > 0 and start >= info.duration):
        raise ValueError(tr("Ungültiger Startzeitpunkt."))
    if end is not None and (not math.isfinite(end) or end <= start or (info.duration > 0 and end > info.duration + .1)):
        raise ValueError(tr("Ungültiger Endzeitpunkt."))
    if not math.isfinite(volume) or not 0 <= volume <= 4:
        raise ValueError(tr("Lautstärke muss zwischen 0 und 4 liegen."))
    tempo = tempo_filters(speed)
    cuts = list(cuts or [])
    frame = 1 / info.fps if info.fps and info.fps > 0 else 1 / 30
    segments = kept_segments(start, end, cuts, info.duration, min_length=frame) if cuts else None
    filters = []
    if crop is not None:
        x, y, w, h = map(int, crop)
        if min(x, y) < 0 or min(w, h) < 2 or x + w > info.width or y + h > info.height:
            raise ValueError(tr("Der Zuschnitt liegt außerhalb des Videos."))
        filters.append(f"crop={w}:{h}:{x}:{y}")
    if size:
        source_width, source_height = (crop[2], crop[3]) if crop is not None else (info.width, info.height)
        new_width, new_height = target_size(source_width, source_height, size, resize_mode)
        if resize_mode == "exact":
            box_width, box_height = _even(size[0]), _even(size[1])
            fitted = target_size(source_width, source_height, size, "fitmax")
            filters.append(f"scale={fitted[0]}:{fitted[1]}:flags=lanczos,pad={box_width}:{box_height}:(ow-iw)/2:(oh-ih)/2")
        elif (new_width, new_height) != (source_width, source_height):
            filters.append(f"scale={new_width}:{new_height}:flags=lanczos")
    elif width:
        if not 16 <= int(width) <= 16384:
            raise ValueError(tr("Ungültige Zielbreite."))
        filters.append(f"scale={int(width)}:-2:flags=lanczos")
    if speed != 1:
        filters.append(f"setpts=PTS/{speed:.8g}")
    filters.append("pad=ceil(iw/2)*2:ceil(ih/2)*2")
    suffix = Path(destination).suffix.lower()
    if segments is not None:
        args = ["-i", str(Path(source).resolve())]
        graph = []
        use_audio = info.has_audio and not mute and suffix not in ANIMATED_FORMATS
        for i, (seg_start, seg_end) in enumerate(segments):
            graph.append(f"[0:v:0]trim=start={seg_start:.6f}:end={seg_end:.6f},setpts=PTS-STARTPTS[v{i}]")
            if use_audio:
                graph.append(f"[0:a:0]atrim=start={seg_start:.6f}:end={seg_end:.6f},asetpts=PTS-STARTPTS[a{i}]")
        if use_audio:
            joined = "".join(f"[v{i}][a{i}]" for i in range(len(segments)))
            graph.append(f"{joined}concat=n={len(segments)}:v=1:a=1[cv][ca]")
        else:
            joined = "".join(f"[v{i}]" for i in range(len(segments)))
            graph.append(f"{joined}concat=n={len(segments)}:v=1:a=0[cv]")
        if suffix == ".gif":
            vchain = ",".join(filters + [f"fps={int(fps)}", "split[ga][gb];[ga]palettegen[gp];[gb][gp]paletteuse"])
            graph.append(f"[cv]{vchain}[outv]")
            args += ["-filter_complex", ";".join(graph), "-map", "[outv]", "-an", "-loop", str(int(loops))]
        elif suffix in ANIMATED_FORMATS:
            vchain = ",".join(filters + [f"fps={int(fps)}"])
            graph.append(f"[cv]{vchain}[outv]")
            args += ["-filter_complex", ";".join(graph), "-map", "[outv]", "-an"] + _codec_options(suffix, quality, loops)
        else:
            graph.append(f"[cv]{','.join(filters)}[outv]")
            if use_audio:
                graph.append(f"[ca]{tempo},volume={volume:.8g}[outa]")
                args += ["-filter_complex", ";".join(graph), "-map", "[outv]", "-map", "[outa]"] + _codec_options(suffix, quality)
            else:
                args += ["-filter_complex", ";".join(graph), "-map", "[outv]", "-an"] + _codec_options(suffix, quality)
        return _render(args, destination, [source])
    seek = ["-ss", f"{start:.6f}"] + (["-t", f"{end - start:.6f}"] if end is not None else [])
    source_args = ["-i", str(Path(source).resolve())]
    output = _plain_output_args(filters, suffix, fps, loops, quality, mute, info, tempo, volume)
    try:
        # Schnell: vor der Eingabe springen. Das braucht ein Schlüsselbild vor `start` und einen Index im Container.
        return _render(seek + source_args + output, destination, [source])
    except (RuntimeError, ValueError) as error:
        if start <= 0 or (isinstance(error, ValueError) and not isinstance(error, NoVideoStreamError)):
            raise
        # Ohne Index (z. B. MPEG-TS) landet der Sprung hinter dem einzigen Schlüsselbild, und es entsteht kein
        # Videobild. Dann genau, aber langsamer: vollständig dekodieren und erst in der Ausgabe ab `start` schreiben.
        LOG.info("Schneller Sprung ergab kein Video, dekodiere vollständig: %s", error)
        return _render(source_args + seek + output, destination, [source])


def _plain_output_args(filters, suffix, fps, loops, quality, mute, info, tempo, volume):
    """Filter- und Kodierargumente für den Export ohne Schnitte."""
    args = []
    if suffix == ".gif":
        graph = ",".join(filters + [f"fps={int(fps)}", "split[a][b];[a]palettegen[p];[b][p]paletteuse"])
        args += ["-filter_complex", graph, "-an", "-loop", str(int(loops))]
    elif suffix in ANIMATED_FORMATS:
        args += ["-vf", ",".join(filters + [f"fps={int(fps)}"]), "-an"] + _codec_options(suffix, quality, loops)
    else:
        args += ["-vf", ",".join(filters), "-map", "0:v:0"]
        if mute or not info.has_audio:
            args += ["-an"]
        else:
            args += ["-map", "0:a:0", "-af", f"{tempo},volume={volume:.8g}"]
        args += _codec_options(suffix, quality)
    return args


def export_html_page(source, destination, quality="medium", **edit_options):
    """HTML5 page that plays the video from a folder next to it ("<name>_dateien")."""
    import html
    import shutil
    from urllib.parse import quote
    destination = Path(destination)
    folder = destination.with_name(destination.stem + "_dateien")
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / (Path(source).stem + ".mp4")
    if Path(source).suffix.lower() == ".mp4" and not edit_options:
        shutil.copy2(source, video)
    else:
        edit_video(source, video, quality=quality, **edit_options)
    info = probe_media(video)
    page = ("<!doctype html>\n<html lang=\"de\"><head><meta charset=\"utf-8\">"
            f"<title>{html.escape(Path(source).stem)}</title>"
            "<style>body{margin:0;background:#202020;display:grid;place-items:center;min-height:100vh}"
            "video{max-width:100%;height:auto}</style></head><body>"
            f"<video controls preload=\"metadata\" width=\"{info.width}\" height=\"{info.height}\" "
            f"src=\"{quote(folder.name)}/{quote(video.name)}\"></video></body></html>\n")
    destination.write_text(page, encoding="utf-8")
    return destination


def merge_videos(sources, destination):
    if len(sources) < 2:
        raise ValueError(tr("Mindestens zwei Videos auswählen."))
    infos = [probe_media(source) for source in sources]
    if any(info.duration <= 0 for info in infos):
        raise ValueError(tr("Die Dauer eines Videos konnte nicht bestimmt werden."))
    width, height = infos[0].width // 2 * 2, infos[0].height // 2 * 2
    args, graph = [], []
    for index, (source, info) in enumerate(zip(sources, infos)):
        args += ["-i", str(Path(source).resolve())]
        graph.append(f"[{index}:v:0]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,setpts=PTS-STARTPTS[v{index}]")
        if info.has_audio:
            graph.append(f"[{index}:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS[a{index}]")
        else:
            graph.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={info.duration:.6f}[a{index}]")
    joined = "".join(f"[v{i}][a{i}]" for i in range(len(sources)))
    graph.append(f"{joined}concat=n={len(sources)}:v=1:a=1[outv][outa]")
    args += ["-filter_complex", ";".join(graph), "-map", "[outv]", "-map", "[outa]", *_codec_options(Path(destination).suffix.lower())]
    return _render(args, destination, sources)
