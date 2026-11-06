"""WASAPI microphone/loopback capture and independently adjustable audio tracks.

Device streams open only after the user starts recording. Tests use synthetic
WAV files and do not record the user's microphone or desktop audio.
"""
from dataclasses import dataclass
from pathlib import Path
import threading
import time
import wave
from .i18n import tr


@dataclass
class AudioClip:
    path: Path
    started: float
    volume: float
    frames: int
    rate: int
    kind: str = ""          # "microphone" or "loopback"; the audio options treat them differently


def audio_chain(clip, video_started, duration, noise_gate_db=0, timeshift_ms=0, loopback_balance=1.0):
    """ffmpeg filter chain for one recorded clip.

    noise_gate_db: microphone only; quieter sound than this level (dB, e.g. -40) is muted; 0 = off.
    timeshift_ms: delays (+) or advances (-) every track against the video to fine-tune lip sync.
    loopback_balance: 0.1..1.0, system sound volume relative to the microphone."""
    if not 0 <= -noise_gate_db <= 90:
        raise ValueError(tr("Der Rauschfilter-Pegel muss zwischen -90 und 0 dB liegen."))
    if not -2000 <= timeshift_ms <= 2000:
        raise ValueError(tr("Der Audio-Zeitversatz muss zwischen -2000 und 2000 ms liegen."))
    if not .1 <= loopback_balance <= 1:
        raise ValueError(tr("Die Systemton-Balance muss zwischen 0,1 und 1,0 liegen."))
    offset = clip.started - video_started + timeshift_ms / 1000
    timing = f"adelay={max(0, round(offset * 1000))}:all=1" if offset >= 0 else f"atrim=start={-offset:.6f},asetpts=PTS-STARTPTS"
    gate = f"agate=threshold={10 ** (noise_gate_db / 20):.6g}:ratio=20:attack=5:release=250," if noise_gate_db and clip.kind == "microphone" else ""
    volume = clip.volume * (loopback_balance if clip.kind == "loopback" else 1)
    return f"aresample=48000,aformat=channel_layouts=stereo,{gate}{timing},volume={volume:.6g},apad,atrim=duration={duration:.6f}"


def input_devices():
    import pyaudiowpatch as pa
    with pa.PyAudio() as manager:
        host = manager.get_host_api_info_by_type(pa.paWASAPI)
        return [{"name": d["name"], "selector": f"wasapi:{int(d['index'])}", "loopback": bool(d.get("isLoopbackDevice"))}
                for d in manager.get_device_info_generator_by_host_api(int(host["index"])) if d["maxInputChannels"] > 0]


class WasapiSession:
    def __init__(self, folder, sources):
        import pyaudiowpatch as pa
        self.pa = pa
        self.manager = pa.PyAudio()
        self.streams, self.files, self.clips = [], [], []
        self.error = None
        self.closed = False
        self.running = False
        try:
            host = self.manager.get_host_api_info_by_type(pa.paWASAPI)
            for index, (kind, selector, volume) in enumerate(sources):
                if not 0 <= volume <= 4:
                    raise ValueError(tr("Ungültige Audio-Lautstärke."))
                if selector in ("default_output", "__wasapi_loopback__"):
                    device = self.manager.get_default_wasapi_loopback()
                elif selector == "default_input":
                    device = self.manager.get_device_info_by_index(host["defaultInputDevice"])
                elif selector.startswith("wasapi:"):
                    device = self.manager.get_device_info_by_index(int(selector.split(":", 1)[1]))
                else:
                    devices = [d for d in self.manager.get_device_info_generator_by_host_api(int(host["index"]))
                               if d["maxInputChannels"] > 0 and selector.casefold() in d["name"].casefold()]
                    if len(devices) != 1:
                        raise RuntimeError(tr("Audioquelle nicht eindeutig gefunden: {source}", source=selector))
                    device = devices[0]
                channels, rate = int(device["maxInputChannels"]), int(device["defaultSampleRate"])
                if channels < 1:
                    raise RuntimeError(tr("Audioquelle hat keine Eingangskanäle."))
                path = Path(folder) / f"audio-{index}-{kind}.wav"
                handle = wave.open(str(path), "wb")
                handle.setnchannels(channels)
                handle.setsampwidth(2)
                handle.setframerate(rate)
                self.files.append(handle)
                clip = AudioClip(path, 0, volume, 0, rate, kind)
                self.clips.append(clip)
                def callback(data, frame_count, timing, status, handle=handle, clip=clip):
                    try:
                        if clip.frames == 0:
                            latency = timing.get("current_time", 0) - timing.get("input_buffer_adc_time", 0)
                            clip.started = time.perf_counter() - (latency if 0 <= latency <= 2 else frame_count / clip.rate)
                        handle.writeframesraw(data)
                        clip.frames += frame_count
                        return (None, pa.paContinue)
                    except Exception as exc:
                        self.error = str(exc)
                        return (None, pa.paAbort)
                self.streams.append(self.manager.open(format=pa.paInt16, channels=channels,
                    rate=rate, input=True, input_device_index=int(device["index"]),
                    frames_per_buffer=1024, stream_callback=callback, start=False))
        except Exception:
            self.close()
            raise

    def start(self):
        try:
            for stream in self.streams:
                stream.start_stream()
            self.running = True
        except Exception:
            self.close()
            raise

    def close(self):
        if self.closed:
            return self.clips
        self.closed = True
        for stream in self.streams:
            try:
                if stream.is_active():
                    stream.stop_stream()
            except Exception as exc:
                self.error = self.error or str(exc)
            try:
                stream.close()
            except Exception as exc:
                self.error = self.error or str(exc)
        for handle in self.files:
            handle.close()
        self.manager.terminate()
        self.running = False
        return self.clips


def mux_recorded_audio(video, clips, video_started, noise_gate_db=0, timeshift_ms=0, loopback_balance=1.0):
    from .media import _render, probe_media
    duration = probe_media(video).duration
    if duration <= 0:
        raise RuntimeError(tr("Videodauer konnte für die Ton-Synchronisierung nicht bestimmt werden."))
    args, graph = ["-i", str(video)], []
    for index, clip in enumerate(clips):
        if clip.frames:
            args += ["-i", str(clip.path)]
            chain = audio_chain(clip, video_started, duration, noise_gate_db, timeshift_ms, loopback_balance)
            graph.append(f"[{index + 1}:a]{chain}[a{index}]")
        else:
            args += ["-f", "lavfi", "-t", str(duration), "-i", "anullsrc=r=48000:cl=stereo"]
            graph.append(f"[{index + 1}:a]anull[a{index}]")
    graph.append("".join(f"[a{i}]" for i in range(len(clips))) + f"amix=inputs={len(clips)}:normalize=0,alimiter=limit=.95:latency=1[mix]")
    output = Path(video).with_name(Path(video).stem + "-mit-ton.mp4")  # i18n: ignore – Gespeicherter Schlüssel oder Dateinamensmuster bleibt sprachunabhängig.
    return _render([*args, "-filter_complex", ";".join(graph), "-map", "0:v:0", "-map", "[mix]",
        "-c:v", "copy", "-c:a", "aac", "-t", str(duration), "-movflags", "+faststart"], output, [video])
