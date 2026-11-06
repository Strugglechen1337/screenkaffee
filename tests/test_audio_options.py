import re
import subprocess
from pathlib import Path
import pytest
from screenkaffee.audio import AudioClip, audio_chain, mux_recorded_audio
from screenkaffee.media import ffmpeg_binary, probe_media
from screenkaffee import capture_options as co


def ffmpeg(*args):
    return subprocess.run([ffmpeg_binary(), "-hide_banner", "-nostdin", "-loglevel", "error", *map(str, args)], check=True, timeout=120)


def mean_volume(path, start=None, duration=None):
    window = (["-ss", str(start)] if start is not None else []) + (["-t", str(duration)] if duration else [])
    result = subprocess.run([ffmpeg_binary(), "-hide_banner", "-nostdin", *window, "-i", str(path), "-af", "volumedetect", "-vn", "-f", "null", "-"],
                            capture_output=True, timeout=120)
    match = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", result.stderr.decode(errors="replace"))
    return -99.0 if match is None or match.group(1) == "-inf" else float(match.group(1))


def clip(tmp_path, name, kind, level, frames=1):
    path = tmp_path / f"{name}.wav"
    ffmpeg("-f", "lavfi", "-i", f"sine=frequency=440:duration=2:sample_rate=48000", "-af", f"volume={level}", path)
    return AudioClip(path, 10.0, 1.0, frames, 48000, kind)


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "bild.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=black:s=64x64:d=2:r=10", "-c:v", "libx264", "-pix_fmt", "yuv420p", path)
    return path


def test_chain_applies_each_option_to_the_right_track(tmp_path):
    mic, loop = clip(tmp_path, "m", "microphone", 1), clip(tmp_path, "l", "loopback", 1)
    mic_chain = audio_chain(mic, 10.0, 2.0, noise_gate_db=-40, timeshift_ms=250, loopback_balance=.5)
    loop_chain = audio_chain(loop, 10.0, 2.0, noise_gate_db=-40, timeshift_ms=250, loopback_balance=.5)
    assert "agate=threshold=0.01" in mic_chain and "agate" not in loop_chain                 # gate: microphone only
    assert "volume=1," in mic_chain and "volume=0.5," in loop_chain                           # balance: loopback only
    assert "adelay=250" in mic_chain and "adelay=250" in loop_chain
    early = AudioClip(tmp_path / "m.wav", 9.9, 1.0, 1, 48000, "microphone")
    assert "atrim=start=" in audio_chain(early, 10.0, 2.0, timeshift_ms=-100)               # audio ahead of the video is trimmed
    assert "agate" not in audio_chain(mic, 10.0, 2.0)


def test_invalid_options_are_rejected(tmp_path):
    track = clip(tmp_path, "m", "microphone", 1)
    for kwargs in ({"noise_gate_db": 5}, {"noise_gate_db": -120}, {"timeshift_ms": 5000}, {"loopback_balance": 0}, {"loopback_balance": 2}):
        with pytest.raises(ValueError):
            audio_chain(track, 10.0, 2.0, **kwargs)


def test_loopback_balance_lowers_only_the_system_sound(tmp_path, video):
    loop = clip(tmp_path, "loop", "loopback", .5)
    full = mux_recorded_audio(video, [loop], 10.0)
    full = full.rename(tmp_path / "voll.mp4")
    quiet = mux_recorded_audio(video, [loop], 10.0, loopback_balance=.2)
    assert mean_volume(full) - mean_volume(quiet) == pytest.approx(14, abs=2)               # 20 * log10(1 / 0.2) = 14 dB
    mic = clip(tmp_path, "mic", "microphone", .5)
    unchanged = mux_recorded_audio(video, [mic], 10.0, loopback_balance=.2)
    assert mean_volume(unchanged) == pytest.approx(mean_volume(full), abs=1.5)


def test_noise_gate_silences_quiet_microphone_but_keeps_speech_level(tmp_path, video):
    hiss, speech = clip(tmp_path, "hiss", "microphone", .01), clip(tmp_path, "speech", "microphone", .5)
    open_gate = mux_recorded_audio(video, [hiss], 10.0).rename(tmp_path / "offen.mp4")
    gated_hiss = mux_recorded_audio(video, [hiss], 10.0, noise_gate_db=-30)
    assert mean_volume(open_gate) > -70 and mean_volume(gated_hiss) < mean_volume(open_gate) - 10
    plain = mux_recorded_audio(video, [speech], 10.0).rename(tmp_path / "sprache.mp4")
    kept = mux_recorded_audio(video, [speech], 10.0, noise_gate_db=-30)
    assert mean_volume(kept) == pytest.approx(mean_volume(plain), abs=2)                      # speech above the threshold passes


def test_time_shift_moves_the_sound_against_the_picture(tmp_path, video):
    tone = clip(tmp_path, "tone", "microphone", .5)
    shifted = mux_recorded_audio(video, [tone], 10.0, timeshift_ms=1000)
    assert probe_media(shifted).duration == pytest.approx(2.0, abs=.2)
    assert mean_volume(shifted, start=0, duration=.8) < -60                                   # silence before the delayed start
    assert mean_volume(shifted, start=1.2, duration=.6) > -40


def test_audio_settings_are_validated_and_used_for_new_recordings(qt_app, tmp_path):
    assert co.validate(dict(co.DEFAULTS)) == {}
    assert set(co.validate(dict(co.DEFAULTS, audio_noise_gate_db=10, audio_timeshift_ms=9999, audio_loopback_balance=5))) == {
        "audio_noise_gate_db", "audio_timeshift_ms", "audio_loopback_balance"}
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    window = MainWindow(Workspace(tmp_path))
    assert window.settings["audio_loopback_balance"] == 100 and window.settings["audio_noise_gate_db"] == 0
