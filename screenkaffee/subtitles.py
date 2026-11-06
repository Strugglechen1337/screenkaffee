"""Offline transcription, editable SRT files and MP4 subtitle tracks."""
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
from .paths import application_root


@dataclass
class Subtitle:
    start: float
    end: float
    text: str


def validate(captions):
    previous = -1
    for caption in captions:
        if not math.isfinite(caption.start) or not math.isfinite(caption.end) or caption.start < 0 or caption.end <= caption.start:
            raise ValueError("Jeder Untertitel benötigt eine gültige Start- und Endzeit.")
        if caption.start < previous:
            raise ValueError("Untertitel müssen nach Beginn sortiert sein.")
        previous = caption.start
    return captions


def timestamp(seconds):
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def write_srt(captions, path):
    validate(captions)
    content = "\n\n".join(f"{index}\n{timestamp(c.start)} --> {timestamp(c.end)}\n{c.text.strip()}" for index, c in enumerate(captions, 1))
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content + "\n", encoding="utf-8")
    temporary.replace(path)


def read_srt(path):
    content = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    captions = []
    for block in re.split(r"\n\s*\n", content.strip()):
        lines = block.splitlines()
        if not lines:
            continue
        if "-->" not in lines[0]:
            lines = lines[1:]
        if not lines:
            continue
        match = re.fullmatch(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})", lines[0])
        if not match:
            raise ValueError("Ungültige Untertitel-Zeitzeile: " + lines[0])
        v = list(map(int, match.groups()))
        start, end = (v[0] * 3600 + v[1] * 60 + v[2] + v[3] / 1000,
                      v[4] * 3600 + v[5] * 60 + v[6] + v[7] / 1000)
        captions.append(Subtitle(start, end, "\n".join(lines[1:])))
    return validate(captions)


def load_model():
    # Only a local model path is passed: no audio or video is uploaded.
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    folder = application_root() / "models" / "whisper-base"
    if not (folder / "model.bin").is_file():
        raise RuntimeError("Die Untertitel-Erweiterung ist nicht installiert. Sie wird beim Klick auf "
                           "„Untertitel lokal erzeugen“ angeboten.")
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Die Untertitel-Erweiterung ist unvollständig. Bitte erneut herunterladen.") from exc
    return WhisperModel(str(folder), device="cpu", compute_type="int8",
        cpu_threads=min(4, max(1, os.cpu_count() or 1)), local_files_only=True)


def self_test_speech():
    """Erweiterung vollständig laden (PyAV, ctranslate2, tokenizers, Modell) und eine Sekunde Stille erkennen."""
    import av  # noqa: F401
    import numpy as np
    segments, info = load_model().transcribe(np.zeros(16000, dtype=np.float32), language="de", beam_size=1, vad_filter=False)
    list(segments)
    return info.duration


def transcribe(path, language=None, progress=None):
    model = load_model()
    segments, info = model.transcribe(str(path), language=language or None, beam_size=5, vad_filter=False)
    captions = []
    for segment in segments:
        if segment.text.strip() and segment.end > segment.start:
            captions.append(Subtitle(max(0, segment.start), segment.end, segment.text.strip()))
        if progress:
            progress(segment.end, info.duration)
    return validate(captions)


def embed_subtitles(video, srt, destination, language="deu"):
    if not re.fullmatch(r"[A-Za-z]{2,3}", language):
        raise ValueError("Ungültige Untertitelsprache.")
    read_srt(srt)
    from .media import _render
    return _render(["-i", str(Path(video).resolve()), "-i", str(Path(srt).resolve()),
        "-map", "0:v:0", "-map", "0:a?", "-map", "1:0", "-c:v", "copy", "-c:a", "copy",
        "-c:s", "mov_text", "-metadata:s:s:0", "language=" + language, "-movflags", "+faststart"], destination, [video])
