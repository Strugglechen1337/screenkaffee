import json
from pathlib import Path
import numpy as np
from PIL import Image
import pytest
from screenkaffee.effects import ImageEffects, apply_effects
from screenkaffee.editor import EditorView, SourceCommand, EffectsCommand
from screenkaffee.media import (ffmpeg_binary, probe_media, edit_video, merge_videos,
                                extract_frame, tempo_filters)
from screenkaffee.integrations import run


def test_finishing_effects_are_reeditable_and_preserve_transparency(qt_app, tmp_path):
    original = Image.new("RGB", (160, 100), "#336699")
    view = EditorView(original)
    effects = ImageEffects(scale_percent=50, corner_radius=15, padding=8,
        transparent_background=True, border_width=2, shadow_radius=3).to_dict()
    view.undo.push(EffectsCommand(view, effects))
    image = view.render_image()
    assert image.mode == "RGBA"
    assert image.getpixel((0, 0))[3] == 0
    reloaded = EditorView(original)
    reloaded.load_project(json.loads(json.dumps(view.project())))
    assert reloaded.render_image().tobytes() == image.tobytes()
    from screenkaffee.color import save_png
    save_png(image, tmp_path / "alpha.png")
    with Image.open(tmp_path / "alpha.png") as saved:
        assert saved.getpixel((0, 0))[3] == 0
    view.undo.undo()
    assert view.render_image().size == original.size


def test_transparent_history_image_stays_transparent_when_reopened(qt_app, tmp_path):
    from screenkaffee.workspace import Workspace
    workspace = Workspace(tmp_path)
    image = Image.new("RGBA", (80, 60), (30, 60, 90, 0))
    ident = workspace.add(image)
    restored = workspace.image(ident)
    assert restored.mode == "RGBA"
    assert EditorView(restored).render_image().getpixel((0, 0))[3] == 0
    workspace.close()


def test_replacement_source_and_new_annotations_survive_project(qt_app):
    original = Image.new("RGB", (200, 100), "white")
    view = EditorView(original)
    view.undo.push(SourceCommand(view, Image.new("RGB", (250, 120), "#224466")))
    for index, kind in enumerate(["speech", "freehand", "polygon", "bracket", "magnifier", "spotlight", "pixelate"]):
        view.add_annotation({"kind": kind, "color": "#ff5555", "width": 3,
            "points": [10 + index * 4, 10, 120, 90], "text": "Beispiel", "path": [[10, 10], [80, 20], [120, 90]],
            "text_width": 150, "text_height": 90, "rotation": 5})
    view.insert_stamp(Image.new("RGBA", (20, 20), (255, 0, 0, 150)))
    before = view.render_image()
    loaded = EditorView(original)
    loaded.load_project(json.loads(json.dumps(view.project())))
    assert loaded.render_image().tobytes() == before.tobytes()
    assert loaded.source.size == (250, 120)


@pytest.fixture
def video_pair(tmp_path):
    binary = ffmpeg_binary()
    first, second = tmp_path / "first.mp4", tmp_path / "second.mp4"
    for path, color, audio in [(first, "red", True), (second, "blue", False)]:
        command = [binary, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s=160x120:r=20:d=2"]
        if audio:
            command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac"]
        result = run(command + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)])
        assert result.returncode == 0, result.stderr.decode(errors="replace")
    return first, second


def test_video_trim_crop_speed_and_audio_are_real(video_pair, tmp_path):
    first, _ = video_pair
    before = first.read_bytes()
    result = edit_video(first, tmp_path / "edited.mp4", start=.25, end=1.75,
                        speed=2, mute=True, crop=(10, 10, 100, 80), width=100)
    info = probe_media(result)
    assert (info.width, info.height) == (100, 80)
    assert .6 <= info.duration <= 1
    assert not info.has_audio
    assert first.read_bytes() == before
    assert extract_frame(result, .1).size == (100, 80)


def test_merge_handles_a_missing_audio_stream(video_pair, tmp_path):
    result = merge_videos(video_pair, tmp_path / "merged.mp4")
    info = probe_media(result)
    assert 3.8 <= info.duration <= 4.3
    assert info.has_audio
    red = np.asarray(extract_frame(result, .5)).mean(axis=(0, 1))
    blue = np.asarray(extract_frame(result, 2.5)).mean(axis=(0, 1))
    assert red[0] > red[2] + 100
    assert blue[2] > blue[0] + 100


def test_invalid_video_edit_preserves_destination(video_pair, tmp_path):
    target = tmp_path / "existing.mp4"
    target.write_bytes(b"keep me")
    with pytest.raises(ValueError):
        edit_video(video_pair[0], target, start=5)
    assert target.read_bytes() == b"keep me"
    assert tempo_filters(.125) == "atempo=0.5,atempo=0.5,atempo=0.5"


def test_portable_ffmpeg_without_system_programs(video_pair, tmp_path, monkeypatch):
    import screenkaffee.media as media
    monkeypatch.setattr(media, "tool", lambda name: None)
    info = media.probe_media(video_pair[0])
    assert info.width == 160 and info.has_audio
    output = media.edit_video(video_pair[0], tmp_path / "portable.mp4", mute=True)
    assert not media.probe_media(output).has_audio


def test_pause_resume_omits_paused_time_and_preserves_recovery(video_pair, tmp_path):
    import shutil
    from screenkaffee.integrations import VideoRecorder
    class FakeSegment:
        process = None
        path = None
        index = 0
        def start(self, folder, **options):
            self.path = Path(folder) / f"part-{self.index}.mp4"
            shutil.copy2(video_pair[self.index % 2], self.path)
            self.index += 1
            self.process = object()
        def stop(self):
            self.process = None
            return self.path
    recorder = VideoRecorder()
    recorder.segment = FakeSegment()
    recorder.start(tmp_path / "recordings", fps=30)
    recorder.pause()
    assert recorder.paused and recorder.active and len(recorder.segments) == 1
    recorder.resume()
    assert not recorder.paused
    output = recorder.stop()
    assert 3.8 <= probe_media(output).duration <= 4.3
    assert not recorder.active
    assert all(path.is_file() for path in recorder.segments)
    assert (recorder.folder / "recovery.json").is_file()


def test_subtitle_embedding_creates_a_real_mp4_track(video_pair, tmp_path):
    from screenkaffee.subtitles import Subtitle, write_srt, embed_subtitles
    captions = tmp_path / "captions.srt"
    write_srt([Subtitle(.1, 1.8, "Hallo screenkaffee")], captions)
    output = embed_subtitles(video_pair[0], captions, tmp_path / "subtitled.mp4")
    from screenkaffee.integrations import tool
    result = run([tool("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(output)])
    streams = json.loads(result.stdout)["streams"]
    assert any(s["codec_type"] == "subtitle" and s["codec_name"] == "mov_text" for s in streams)
    assert probe_media(output).has_audio


def test_audio_tracks_are_mixed_with_gain_and_offset_without_recording_devices(video_pair, tmp_path):
    import wave
    from screenkaffee.audio import AudioClip, mux_recorded_audio
    rate = 48000
    samples = np.arange(rate * 2) / rate
    clips = []
    for index, (frequency, gain, offset) in enumerate([(220, .5, 0), (440, .25, .4)]):
        path = tmp_path / f"audio-{index}.wav"
        pcm = np.rint(np.sin(2 * np.pi * frequency * samples) * 12000).astype("<i2")
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            audio.writeframes(pcm.tobytes())
        clips.append(AudioClip(path, 10 + offset, gain, len(pcm), rate))
    output = mux_recorded_audio(video_pair[1], clips, 10)
    assert probe_media(output).has_audio
    result = run([ffmpeg_binary(), "-v", "error", "-i", str(output), "-map", "0:a:0", "-f", "f32le", "-ac", "1", "-ar", str(rate), "-"])
    signal = np.frombuffer(result.stdout, dtype="<f4")
    assert 1.9 <= len(signal) / rate <= 2.1
    def amplitude(start, frequency):
        segment = signal[round(start * rate):round((start + .2) * rate)]
        return abs(np.sum(segment * np.exp(-2j * np.pi * frequency * np.arange(len(segment)) / rate))) / len(segment)
    assert amplitude(.1, 440) < amplitude(.8, 440) / 10
    assert amplitude(.8, 220) > amplitude(.8, 440) * 1.5


def test_annotation_drop_shadow_auto_canvas_and_cutout_gap_torn(qt_app):
    from screenkaffee.effects import cut_out
    base = Image.new("RGB", (120, 80), "white")
    view = EditorView(base)
    view.add_annotation({"kind": "rectangle", "color": "#ff0000", "width": 4, "points": [20, 20, 80, 60]})
    plain = view.render_image().convert("RGB")
    view.annotations()[0].data["shadow_radius"] = 6
    view.annotations()[0].data["shadow_offset"] = 5
    shadowed = view.render_image().convert("RGB")
    assert plain.tobytes() != shadowed.tobytes()

    view.add_annotation({"kind": "rectangle", "color": "#0000ff", "width": 4, "points": [80, 20, 160, 60]})
    assert view.render_image().size == (120, 80)
    view.effects["auto_canvas"] = True
    expanded = view.render_image()
    assert expanded.width > 150 and expanded.height >= 80

    cut = cut_out(Image.new("RGB", (100, 80), "#336699"), "y", 20, 40, gap=10, torn=4)
    assert cut.size == (100, 70)
