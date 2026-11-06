import subprocess
from screenkaffee.media import ffmpeg_binary
from screenkaffee.media_properties import media_properties, parse_ffmpeg_info

SAMPLE = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'a.mp4':
  Duration: 00:00:01.00, start: 0.000000, bitrate: 312 kb/s
  Stream #0:0[0x1](und): Video: h264 (High), yuv420p, 64x64, 25 fps
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, mono, fltp, 69 kb/s (default)
"""


def test_parse_sample():
    info = parse_ffmpeg_info(SAMPLE)
    assert info["Container"].startswith("mov,mp4")
    assert info["Gesamtbitrate (kbit/s)"] == 312
    assert info["Audiocodec"] == "aac" and info["Abtastrate (Hz)"] == 44100
    assert info["Audiokanäle"] == "mono" and info["Audiobitrate (kbit/s)"] == 69


def test_real_video_details(tmp_path):
    out = tmp_path / "t.mp4"
    subprocess.run([ffmpeg_binary(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)], check=True)
    info = media_properties(out, "video")
    assert info["Audiocodec"] == "aac" and info["Abtastrate (Hz)"] == 44100
    assert "mp4" in info["Container"]
