import subprocess
from pathlib import Path
from screenkaffee.media import ffmpeg_binary


def test_recorder_uses_a_stats_period_the_bundled_ffmpeg_accepts():
    source = (Path(__file__).parent.parent / "screenkaffee" / "integrations.py").read_text(encoding="utf-8")
    assert '"-stats_period", "0.1"' in source
    result = subprocess.run([ffmpeg_binary(), "-hide_banner", "-progress", "pipe:1", "-stats_period", "0.1",
                             "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.3", "-f", "null", "-"],
                            capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    rejected = subprocess.run([ffmpeg_binary(), "-hide_banner", "-stats_period", ".1", "-f", "lavfi", "-i", "color=d=0.1", "-f", "null", "-"],
                              capture_output=True, timeout=60)
    if rejected.returncode == 0:
        return
    assert b"stats_period" in rejected.stderr
