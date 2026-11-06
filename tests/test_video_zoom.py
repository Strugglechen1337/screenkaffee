import json
import shutil
import pytest
from screenkaffee.integrations import run, VideoRecorder
from screenkaffee.media import ffmpeg_binary, probe_media, extract_frame
from screenkaffee.video_zoom import ZoomEvent, apply_video_zoom


@pytest.fixture
def zoom_source(tmp_path):
    path = tmp_path / "source.mp4"
    result = run([ffmpeg_binary(), "-v", "error", "-f", "lavfi", "-i",
        "color=c=red:s=320x180:r=10:d=2,drawbox=x=160:y=0:w=160:h=180:c=blue:t=fill",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)])
    assert result.returncode == 0, result.stderr
    return path


def test_zoom_reframes_at_recorded_time_preserves_audio_and_resets(zoom_source, tmp_path):
    original = zoom_source.read_bytes()
    output = apply_video_zoom(zoom_source, tmp_path / "zoomed.mp4", [ZoomEvent(.5, 2, .75, .5), ZoomEvent(1.5, 1)])
    info = probe_media(output)
    assert (info.width, info.height) == (320, 180)
    assert info.has_audio and info.duration == pytest.approx(2, abs=.15)
    assert extract_frame(output, .2).getpixel((40, 90))[0] > 220
    assert extract_frame(output, 1).getpixel((40, 90))[2] > 220
    assert extract_frame(output, 1.8).getpixel((40, 90))[0] > 220
    assert zoom_source.read_bytes() == original
    with pytest.raises(ValueError):
        ZoomEvent(0, float("nan"))
    with pytest.raises(ValueError):
        apply_video_zoom(zoom_source, tmp_path / "invalid.mp4", [ZoomEvent(1, 2), ZoomEvent(0, 1)])


def test_recording_zoom_manifest_omits_pause_time_and_keeps_original(zoom_source, tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("screenkaffee.integrations.time.perf_counter", lambda: clock[0])
    class Segment:
        process = None
        video_anchor = None
        video_started = 0
        index = 0
        def start(self, folder, **options):
            self.video_started = clock[0]
            self.path = folder / f"segment-{self.index}.mp4"
            shutil.copy2(zoom_source, self.path)
            self.index += 1
            self.process = object()
        def stop(self):
            self.process = None
            return self.path
    recorder = VideoRecorder()
    recorder.segment = Segment()
    recorder.start(tmp_path / "recordings")
    clock[0] = 100.5
    recorder.set_zoom(2, .75, .5)
    recorder.pause()
    clock[0] = 500
    recorder.resume()
    clock[0] = 500.5
    recorder.set_zoom(1)
    assert recorder.zoom_events[1].seconds == pytest.approx(2.5, abs=.1)
    path = recorder.stop()
    assert path.is_file() and probe_media(path).has_audio
    assert (recorder.folder / "unzoomed.mp4").is_file()
    manifest = json.loads((recorder.folder / "recovery.json").read_text())
    assert len(manifest["zoom"]) == 2 and not manifest["active"]
    assert all(part.is_file() for part in recorder.segments)
