"""Optional local tools. Never download/upload or execute shell command strings."""
import logging
from datetime import datetime
from pathlib import Path
import io
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from PIL import Image
from .color import import_srgb
from .paths import application_root
from .i18n import tr


LOG = logging.getLogger(__name__)


def tool(name):
    root = application_root()
    executable = name + (".exe" if sys.platform == "win32" else "")
    candidates = [root / "tools" / executable, root / "tools" / name / executable]
    return next((str(p) for p in candidates if p.is_file()), None) or shutil.which(name)


def run(args, timeout=30, input=None):
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.run(args, input=input, capture_output=True, timeout=timeout, creationflags=flags)


def hyprland_window_rectangles():
    """Visible Hyprland windows on the active workspace(s) as (title, (left, top, right, bottom))."""
    hyprctl = tool("hyprctl")
    if not hyprctl:
        return []
    try:
        monitors_res = run([hyprctl, "monitors", "-j"])
        active_ws = {m.get("activeWorkspace", {}).get("id") for m in json.loads(monitors_res.stdout or b"[]") if isinstance(m, dict)} if not monitors_res.returncode else set()
        result = run([hyprctl, "clients", "-j"])
        if result.returncode:
            return []
        clients = json.loads(result.stdout or b"[]")
    except Exception:
        return []
    windows = []
    for c in clients:
        if not isinstance(c, dict) or c.get("hidden") or not c.get("mapped", True):
            continue
        ws = c.get("workspace", {}).get("id")
        if active_ws and ws not in active_ws:
            continue
        at, size = c.get("at") or [0, 0], c.get("size") or [0, 0]
        if len(at) == 2 and len(size) == 2 and min(size) > 10:
            left, top = int(at[0]), int(at[1])
            windows.append((str(c.get("title") or c.get("class") or tr("Fenster")), (left, top, left + int(size[0]), top + int(size[1]))))
    return windows


def omarchy_capture(kind="region", include_cursor=False, return_region=False, monitor_name=None):
    grim = tool("grim")
    if not grim:
        raise RuntimeError(tr("grim fehlt. Alternativ kann die Aufnahme über das Desktop-Portal erfolgen."))
    args = [grim, "-t", "png"]
    bounds = None
    if include_cursor:
        args.append("-c")
    if kind == "window":
        hyprctl = tool("hyprctl")
        if not hyprctl:
            raise RuntimeError(tr("Fensteraufnahme benötigt Hyprland / hyprctl."))
        result = run([hyprctl, "activewindow", "-j"])
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace"))
        window = json.loads(result.stdout)
        if not window.get("size") or min(window["size"]) < 1:
            raise RuntimeError(tr("Kein aktives Fenster gefunden."))
        x, y = window["at"]
        width, height = window["size"]
        bounds = (x, y, width, height)
        args += ["-g", f"{x},{y} {width}x{height}"]
    elif kind in ("region", "scroll", "video", "manual-scroll", "timelapse"):
        slurp = tool("slurp")
        if not slurp:
            raise RuntimeError(tr("Bereichsauswahl benötigt slurp oder das Desktop-Portal."))
        result = run([slurp], timeout=120)
        if result.returncode:
            return None
        geometry = result.stdout.decode().strip()
        match = re.fullmatch(r"(-?\d+),(-?\d+) (\d+)x(\d+)", geometry)
        if not match:
            raise RuntimeError(tr("Ungültige Geometrie von slurp."))
        bounds = tuple(map(int, match.groups()))
        args += ["-g", geometry]
    elif kind == "screen" and monitor_name:
        args += ["-o", monitor_name]
    args.append("-")
    result = run(args)
    if result.returncode:
        raise RuntimeError("grim: " + result.stderr.decode(errors="replace"))
    with Image.open(io.BytesIO(result.stdout)) as image:
        image.load()
        # Use the same color-profile conversion used by file imports.
        from PIL import ImageCms
        if image.info.get("icc_profile"):
            converted = ImageCms.profileToProfile(image,
                ImageCms.ImageCmsProfile(io.BytesIO(image.info["icc_profile"])),
                ImageCms.createProfile("sRGB"), outputMode="RGB")
        else:
            converted = image.convert("RGB")
        return (converted, bounds) if return_region else converted


def ocr(image, language="deu+eng"):
    try:
        from .ocr import recognize
        return recognize(image, language).text
    except ImportError:
        pass
    executable = tool("tesseract")
    if not executable:
        raise RuntimeError(tr("OCR benötigt Tesseract plus deu/eng-Sprachdateien.\n"
            "Portable: tesseract und tessdata in den tools/tesseract-Ordner legen.\n"
            "Omarchy: vorhandenes Tesseract wird automatisch erkannt."))
    with tempfile.TemporaryDirectory(prefix="prism-ocr-") as folder:
        source = Path(folder) / "input.png"
        image.save(source)
        args = [executable, str(source), "stdout", "-l", language]
        tessdata = Path(executable).parent / "tessdata"
        if tessdata.is_dir():
            args += ["--tessdata-dir", str(tessdata)]
        result = run(args, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace"))
        return result.stdout.decode("utf-8", errors="replace")


def android_capture():
    executable = tool("adb")
    if not executable:
        raise RuntimeError(tr("Android-Aufnahme benötigt adb in tools/ oder im PATH sowie aktiviertes USB-Debugging."))
    result = run([executable, "devices"])
    devices = [line.split()[0] for line in result.stdout.decode().splitlines()[1:] if line.endswith("\tdevice")]
    if len(devices) != 1:
        raise RuntimeError(tr("Bitte genau ein Android-Gerät verbinden und USB-Debugging am Gerät bestätigen."))
    result = run([executable, "-s", devices[0], "exec-out", "screencap", "-p"])
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    with Image.open(io.BytesIO(result.stdout)) as image:
        return image.convert("RGB")


class SegmentRecorder:
    """SDR recording only. HDR screenshots use an entirely separate FP16 path."""
    def __init__(self):
        self.process = None
        self.path = None
        self.stderr_file = None
        self.audio_postmix = []
        self.audio_session = None
        self.progress_thread = None
        self.video_started = 0
        self.video_anchor = None

    def start(self, folder, fps=30, microphone=None, region=None, quality="high", system_audio=None,
              microphone_volume=1, system_volume=1, portal=False, audio_noise_gate_db=0, audio_timeshift_ms=0,
              audio_loopback_balance=1.0):
        if self.process:
            raise RuntimeError(tr("Aufnahme läuft bereits."))
        self.audio_options = {"noise_gate_db": audio_noise_gate_db, "timeshift_ms": audio_timeshift_ms,
                              "loopback_balance": audio_loopback_balance}
        if not (0 <= microphone_volume <= 4 and 0 <= system_volume <= 4):
            raise ValueError(tr("Audio-Lautstärken müssen zwischen 0 und 4 liegen."))
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / (datetime.now().strftime("Video-%Y%m%d-%H%M%S-") + os.urandom(3).hex() + ".mp4")  # i18n: ignore – Gespeicherter Schlüssel oder Dateinamensmuster bleibt sprachunabhängig.
        if sys.platform == "win32":
            executable = tool("ffmpeg")
            if not executable:
                try:
                    import imageio_ffmpeg
                    executable = imageio_ffmpeg.get_ffmpeg_exe()
                except ImportError:
                    raise RuntimeError(tr("Video benötigt ffmpeg in tools/ oder im PATH."))
            args = [executable, "-hide_banner", "-thread_queue_size", "1024", "-f", "gdigrab", "-framerate", str(fps)]
            if region:
                x, y, width, height = map(int, region)
                if min(width, height) < 2:
                    raise ValueError(tr("Ungültiger Videoaufnahmebereich."))
                args += ["-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{width}x{height}"]
            args += ["-i", "desktop"]
            crf = {"standard": "26", "high": "20", "ultra": "15"}.get(quality)
            if crf is None:
                raise ValueError(tr("Unbekannte Videoqualitätsstufe."))
            args += ["-progress", "pipe:1", "-stats_period", "0.1", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                     "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-crf", crf,
                     "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(self.path)]
            sources = [(kind, device, volume) for kind, device, volume in [
                ("microphone", microphone, microphone_volume), ("loopback", system_audio, system_volume)] if device and volume > 0]
            if sources:
                from .audio import WasapiSession
                self.audio_session = WasapiSession(folder, sources)
        else:
            executable = tool("gpu-screen-recorder")
            self.audio_postmix = []
            if executable:
                if portal:
                    target = "portal"
                elif region:
                    x, y, width, height = map(int, region)
                    target = f"{width}x{height}+{x}+{y}"
                else:
                    hyprctl = tool("hyprctl")
                    if not hyprctl:
                        raise RuntimeError(tr("Die Monitorwahl benötigt hyprctl oder den Portal-Modus."))
                    result = run([hyprctl, "monitors", "-j"])
                    monitors = json.loads(result.stdout)
                    target = next((m["name"] for m in monitors if m.get("focused")), monitors[0]["name"])
                args = [executable, "-w", target, "-k", "auto", "-f", str(fps), "-fm", "cfr",
                        "-q", {"standard": "high", "high": "very_high", "ultra": "ultra"}.get(quality, "very_high"),
                        "-fallback-cpu-encoding", "yes", "-o", str(self.path)]
                for device, volume in [(microphone, microphone_volume), (system_audio, system_volume)]:
                    if device:
                        args += ["-a", device]
                        self.audio_postmix.append(volume)
                if self.audio_postmix:
                    args += ["-ac", "aac"]
            else:
                executable = tool("wf-recorder")
                if not executable:
                    raise RuntimeError(tr("Wayland-Video benötigt gpu-screen-recorder (Omarchy) oder wf-recorder."))
                if portal:
                    raise RuntimeError(tr("Der Portal-Modus benötigt gpu-screen-recorder."))
                args = [executable, "-f", str(self.path), "-r", str(fps), "-c", "libx264"]
                if region:
                    x, y, width, height = map(int, region)
                    args += ["-g", f"{x},{y} {width}x{height}"]
                args += ["-p", "crf=" + {"standard": "26", "high": "20", "ultra": "15"}.get(quality, "20")]
                if system_audio and microphone:
                    raise RuntimeError(tr("Zwei Audioquellen benötigen gpu-screen-recorder. Es wurde keine Quelle stillschweigend weggelassen."))
                device = microphone or system_audio
                if device:
                    args += ["--audio=" + device]
                    self.audio_postmix = [microphone_volume if microphone else system_volume]
        self.stderr_file = tempfile.TemporaryFile()
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self.video_anchor = None
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE if sys.platform == "win32" else subprocess.DEVNULL,
                stderr=self.stderr_file, creationflags=flags)
            self.video_started = time.perf_counter()
            if sys.platform == "win32":
                self.progress_thread = threading.Thread(target=self.read_progress, args=(self.process,), daemon=True)
                self.progress_thread.start()
            if self.audio_session:
                self.audio_session.start()
        except Exception:
            if self.audio_session:
                self.audio_session.close()
                self.audio_session = None
            if self.process:
                self.process.terminate()
                self.process.wait(timeout=5)
                self.process = None
            self.stderr_file.close()
            self.stderr_file = None
            raise

    def read_progress(self, process):
        try:
            for line in iter(process.stdout.readline, b""):
                if line.startswith(b"out_time_us=") and self.video_anchor is None:
                    try:
                        seconds = int(line.split(b"=", 1)[1]) / 1_000_000
                        if seconds > 0:
                            self.video_anchor = time.perf_counter() - seconds
                    except ValueError:
                        pass
        except (OSError, ValueError):
            pass

    def error_if_stopped(self):
        if self.audio_session and self.audio_session.error:
            error = self.audio_session.error
            try:
                self.stop()
            except Exception:
                LOG.warning("Aufnahme konnte nach Audiofehler nicht sauber beendet werden", exc_info=True)
            return tr("Audioaufnahme fehlgeschlagen; Aufnahme beendet: {error}", error=error)
        if self.process and self.process.poll() is not None:
            self.stderr_file.seek(0)
            text = self.stderr_file.read().decode(errors="replace")[-3000:]
            self.stderr_file.close()
            if self.process.stdin:
                self.process.stdin.close()
            if self.progress_thread:
                self.progress_thread.join(timeout=1)
            if self.audio_session:
                self.audio_session.close()
                self.audio_session = None
            self.stderr_file = None
            self.process = None
            return text or tr("Recorder wurde beendet.")
        return None

    def stop(self):
        if not self.process:
            return None
        process = self.process
        try:
            clips = self.audio_session.close() if self.audio_session else None
            if sys.platform == "win32":
                process.stdin.write(b"q\n")
                process.stdin.flush()
            else:
                import signal
                process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
            if process.returncode not in (0, 255, -2):
                self.stderr_file.seek(0)
                raise RuntimeError(self.stderr_file.read().decode(errors="replace")[-3000:])
            if self.progress_thread:
                self.progress_thread.join(timeout=1)
            if self.audio_session:
                from .audio import mux_recorded_audio
                if self.audio_session.error:
                    raise RuntimeError(self.audio_session.error)
                return mux_recorded_audio(self.path, clips, self.video_anchor or self.video_started, **getattr(self, "audio_options", {}))
            if self.audio_postmix and (len(self.audio_postmix) > 1 or self.audio_postmix[0] != 1):
                from .media import ffmpeg_binary, _render
                graph = [f"[0:a:{i}]volume={v:.6g}[a{i}]" for i, v in enumerate(self.audio_postmix)]
                graph.append("".join(f"[a{i}]" for i in range(len(self.audio_postmix))) + f"amix=inputs={len(self.audio_postmix)}:normalize=0,alimiter=limit=.95[mix]")
                mixed = self.path.with_name(self.path.stem + "-gemischt.mp4")
                return _render(["-i", str(self.path), "-filter_complex", ";".join(graph), "-map", "0:v:0",
                    "-map", "[mix]", "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart"], mixed, [self.path])
            return self.path
        finally:
            if self.audio_session:
                self.audio_session.close()
                self.audio_session = None
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()
            self.stderr_file.close()
            self.stderr_file = None
            self.process = None


class VideoRecorder:
    """Pause/resume uses real segments, so paused time is absent from the result.
    Segments and a manifest survive failed exports and interrupted recordings.
    """
    def __init__(self):
        self.segment = SegmentRecorder()
        self.segments = []
        self.paused = False
        self.active = False
        self.options = {}
        self.path = None
        self.folder = None
        self.zoom_events = []
        self.elapsed_before = 0
        self.segment_started = 0

    def set_zoom(self, factor, x=.5, y=.5):
        if not self.active:
            return
        from .video_zoom import ZoomEvent
        anchor = getattr(self.segment, "video_anchor", None) or self.segment_started
        seconds = self.elapsed_before + (0 if self.paused else max(0, time.perf_counter() - anchor))
        event = ZoomEvent(seconds, factor, x, y)
        if self.zoom_events and event.seconds < self.zoom_events[-1].seconds:
            event = ZoomEvent(self.zoom_events[-1].seconds, factor, x, y)
        self.zoom_events.append(event)
        self.manifest()

    @property
    def process(self):
        return self.segment.process

    def manifest(self):
        if not self.folder:
            return
        from .video_zoom import serialize_events
        content = {"output": str(self.path), "segments": [str(path) for path in self.segments], "zoom": serialize_events(self.zoom_events),
                   "current": str(self.segment.path) if self.segment.process else None, "active": self.active,
                   "paused": self.paused, "options": self.options}
        target = self.folder / "recovery.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(content, indent=2), encoding="utf-8")
        temporary.replace(target)

    def start(self, folder, **options):
        if self.active:
            raise RuntimeError(tr("Eine Videoaufnahme läuft bereits oder ist pausiert."))
        base = Path(folder)
        base.mkdir(parents=True, exist_ok=True)
        ident = os.urandom(6).hex()
        self.path = base / (datetime.now().strftime("Video-%Y%m%d-%H%M%S-") + ident + ".mp4")  # i18n: ignore – Gespeicherter Schlüssel oder Dateinamensmuster bleibt sprachunabhängig.
        self.folder = base / "recording-parts" / ident
        self.folder.mkdir(parents=True)
        self.options, self.segments = dict(options), []
        self.zoom_events, self.elapsed_before = [], 0
        self.segment.start(self.folder, **self.options)
        self.segment_started = getattr(self.segment, "video_started", 0) or time.perf_counter()
        self.active, self.paused = True, False
        self.manifest()

    def pause(self):
        if not self.active or self.paused:
            return
        path = self.segment.stop()
        if path:
            self.segments.append(path)
            from .media import probe_media
            self.elapsed_before += probe_media(path).duration
        self.paused = True
        self.manifest()

    def resume(self):
        if not self.active or not self.paused:
            return
        self.segment.start(self.folder, **self.options)
        self.segment_started = getattr(self.segment, "video_started", 0) or time.perf_counter()
        self.paused = False
        self.manifest()

    def error_if_stopped(self):
        error = self.segment.error_if_stopped()
        if error:
            self.active = False
            self.manifest()
        return error

    def stop(self):
        if not self.active:
            return None
        if not self.paused:
            path = self.segment.stop()
            if path:
                self.segments.append(path)
        self.paused = True
        self.manifest()
        if not self.segments:
            raise RuntimeError(tr("Die Aufnahme enthält keine Videosegmente."))
        from .media import merge_videos, probe_media
        output = self.folder / "unzoomed.mp4" if self.zoom_events else self.path
        if len(self.segments) == 1:
            probe_media(self.segments[0])
            shutil.copy2(self.segments[0], output)
        else:
            merge_videos(self.segments, output)
        if self.zoom_events:
            from .video_zoom import apply_video_zoom
            apply_video_zoom(output, self.path, self.zoom_events)
        self.active, self.paused = False, False
        self.manifest()
        return self.path


def _android_device(executable):
    result = run([executable, "devices"])
    devices = [line.split()[0] for line in result.stdout.decode().splitlines()[1:] if line.endswith("\tdevice")]
    if len(devices) != 1:
        raise RuntimeError(tr("Bitte genau ein Android-Gerät verbinden und USB-Debugging am Gerät bestätigen."))
    return devices[0]


class AndroidRecorder:
    """Records the device screen with adb screenrecord (device limit: 3 minutes per run)."""
    remote = "/sdcard/screenkaffee-recording.mp4"

    def __init__(self):
        self.process = None
        self.adb = None
        self.device = None

    @property
    def active(self):
        return self.process is not None

    def start(self):
        if self.process:
            raise RuntimeError(tr("Android-Aufnahme läuft bereits."))
        self.adb = tool("adb")
        if not self.adb:
            raise RuntimeError(tr("Android-Aufnahme benötigt adb in tools/ oder im PATH sowie aktiviertes USB-Debugging."))
        self.device = _android_device(self.adb)
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.process = subprocess.Popen([self.adb, "-s", self.device, "shell", "screenrecord", self.remote],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)

    def stop(self, folder):
        if not self.process:
            return None
        process, self.process = self.process, None
        run([self.adb, "-s", self.device, "shell", "pkill", "-2", "screenrecord"])
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (datetime.now().strftime("Android-%Y%m%d-%H%M%S-") + os.urandom(3).hex() + ".mp4")
        result = run([self.adb, "-s", self.device, "pull", self.remote, str(target)], timeout=120)
        run([self.adb, "-s", self.device, "shell", "rm", self.remote])
        if result.returncode or not target.exists():
            raise RuntimeError(tr("Das Android-Video konnte nicht vom Gerät geladen werden: {error}", error=result.stderr.decode(errors="replace")))
        return target
