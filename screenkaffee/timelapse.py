"""Zeitraffer: in festen Abständen ein Bild aufnehmen und daraus am Ende ein Video machen."""
from datetime import datetime
from pathlib import Path
import shutil
import threading
import time
import logging

LOG = logging.getLogger(__name__)
PATTERN = "frame_%06d.jpg"
MAX_FAILURES = 5          # so viele Fehlversuche hintereinander beenden den Zeitraffer
STOP_TIMEOUT = 30         # so lange wartet stop() auf das Ende des Aufnahme-Threads


def video_seconds(duration_seconds, interval, fps):
    """Wie lang das Video aus einer Aufnahmedauer wird."""
    return duration_seconds / max(0.1, interval) / max(1, fps)


def describe(interval, fps):
    """'1 Stunde Aufnahme → 24 s Video (150× schneller)'."""
    seconds = video_seconds(3600, interval, fps)
    speed = interval * fps
    length = f"{seconds:.0f} s" if seconds < 120 else f"{seconds / 60:.1f} min".replace(".", ",")
    return f"1 Stunde Aufnahme → {length} Video ({speed:g}× schneller)"


class Timelapse:
    """Nimmt im Hintergrund alle `interval` Sekunden ein Bild mit `grab()` auf (gibt ein PIL-Bild zurück)."""

    def __init__(self, folder, grab, interval=5.0, fps=30, max_seconds=0, clock=time.monotonic):
        if interval <= 0 or not 1 <= int(fps) <= 60:
            raise ValueError("Ungültiger Abstand oder ungültige Bildrate.")
        self.folder = Path(folder) / ("timelapse-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        self.grab, self.interval, self.fps, self.max_seconds, self.clock = grab, float(interval), int(fps), max_seconds, clock
        self.frames = 0
        self.failures = 0
        self.last_error = None
        self.size = None
        self.started = None
        self.finished_reason = None
        self.stop_event = threading.Event()
        self.thread = None

    @property
    def active(self):
        return self.thread is not None and self.thread.is_alive()

    def elapsed(self):
        return 0 if self.started is None else self.clock() - self.started

    def start(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        self.started = self.clock()
        self.thread = threading.Thread(target=self.run, name="screenkaffee-timelapse", daemon=True)
        self.thread.start()

    def run(self):
        next_shot = self.clock()
        while not self.stop_event.is_set():
            self.shoot()
            if self.failures >= MAX_FAILURES:
                self.finished_reason = "Aufnahme mehrfach fehlgeschlagen: " + str(self.last_error)
                break
            if self.max_seconds and self.elapsed() >= self.max_seconds:
                self.finished_reason = "Höchstdauer erreicht"
                break
            next_shot += self.interval
            self.stop_event.wait(max(0.0, next_shot - self.clock()))
        self.stop_event.set()

    def shoot(self):
        try:
            image = self.grab().convert("RGB")
            if self.size is None:
                self.size = (image.width // 2 * 2, image.height // 2 * 2)      # H.264 braucht gerade Maße
            if image.size != self.size:
                from PIL import Image
                image = image.resize(self.size, Image.Resampling.LANCZOS)
            image.save(self.folder / (PATTERN % (self.frames + 1)), "JPEG", quality=92)
            self.frames += 1
            self.failures = 0
        except Exception as exc:
            self.failures += 1
            self.last_error = exc
            LOG.warning("Zeitraffer-Bild fehlgeschlagen", exc_info=True)

    def stop(self, timeout=None):
        """Beendet die Aufnahme; True, wenn der Aufnahme-Thread wirklich beendet ist."""
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=STOP_TIMEOUT if timeout is None else timeout)
        return not self.active

    def render(self, destination=None, timeout=None):
        """Bilder zu einem MP4 zusammensetzen; danach werden die Einzelbilder gelöscht (bei Fehlern bleiben sie)."""
        if not self.stop(timeout):
            # Ein hängendes Bild läuft noch: weder rendern noch löschen, sonst geht es verloren oder landet im Nichts.
            raise RuntimeError("Die Zeitraffer-Aufnahme reagiert nicht (die letzte Bildaufnahme hängt). "
                               f"Das Video wurde nicht erstellt; die bisherigen {self.frames} Einzelbilder bleiben "
                               f"erhalten in:\n{self.folder}")
        if self.frames < 2:
            self.discard()
            raise RuntimeError("Zu wenige Bilder für einen Zeitraffer (mindestens 2). Bitte länger aufnehmen.")
        from .media import _codec_options, _render
        destination = Path(destination or self.folder.with_suffix(".mp4"))
        args = ["-framerate", str(self.fps), "-i", str(self.folder / PATTERN), "-an", *_codec_options(".mp4", "high")]
        _render(args, destination, [])
        self.discard()
        return destination

    def discard(self):
        shutil.rmtree(self.folder, ignore_errors=True)
