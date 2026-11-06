import subprocess
import pytest
from PIL import Image
from screenkaffee import cli
from screenkaffee.media import ffmpeg_binary, probe_media


def test_rect_parsing():
    assert cli.parse_rect("0;0;640;480") == (0, 0, 640, 480)
    assert cli.parse_rect("-1;-1;-1;-1") is None
    assert cli.parse_rect("10,20,30,40") == (10, 20, 30, 40)
    for bad in ("1;2;3", "a;b;c;d", "0;0;1;1"):
        with pytest.raises(ValueError):
            cli.parse_rect(bad)


def test_capture_saves_the_given_region_to_the_file(tmp_path):
    seen = []

    def fake(bounds):
        seen.append(tuple(bounds))
        return Image.new("RGB", (bounds[2], bounds[3]), "orange"), {"backend": "test"}

    target = tmp_path / "ordner" / "aus.png"
    assert cli.run_capture(["--screenshotregion", "--rect", "5;6;40;30", "--filepath", str(target)], capture=fake) == 0
    assert seen == [(5, 6, 40, 30)]
    with Image.open(target) as image:
        assert image.size == (40, 30)


def test_capture_accepts_single_dash_options_and_full_desktop(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "desktop_bounds", lambda: (0, 0, 64, 48))
    seen = []
    target = tmp_path / "voll.jpg"
    code = cli.run_capture(["-screenshotregion", "-rect", "-1;-1;-1;-1", "-filepath", str(target)],
                           capture=lambda b: (seen.append(tuple(b)) or Image.new("RGB", (b[2], b[3]), "white"), {}))
    assert code == 0 and seen == [(0, 0, 64, 48)] and target.exists()


def test_capture_without_rect_points_to_the_interactive_mode():
    with pytest.raises(SystemExit):
        cli.run_capture(["--screenshotregion"], capture=lambda b: None)


def test_dispatch_only_takes_over_known_commands():
    assert cli.dispatch(["--diagnose"]) is None
    assert cli.dispatch([]) is None
    assert cli.dispatch(["bild.png"]) is None


def make_video(path, color):
    subprocess.run([ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s=160x90:d=1:r=15",
                    "-f", "lavfi", "-i", "sine=frequency=440:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(path)], check=True, timeout=120)


def test_videos_merge_joins_files_next_to_the_first(tmp_path, capsys):
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
    make_video(first, "red")
    make_video(second, "blue")
    assert cli.dispatch(["videos", "--merge", str(first), str(second)]) == 0
    merged = tmp_path / "a-kombiniert.mp4"
    assert merged.exists() and probe_media(merged).duration > 1.5
    assert str(merged) in capsys.readouterr().out
    custom = tmp_path / "ziel.mp4"
    assert cli.dispatch(["videos", "--merge", str(first), str(second), "--output", str(custom)]) == 0 and custom.exists()


def test_videos_merge_needs_two_files_and_reports_errors(tmp_path, capsys):
    with pytest.raises(SystemExit):
        cli.run_videos(["--merge", str(tmp_path / "a.mp4")])
    assert cli.dispatch(["videos", "--merge", str(tmp_path / "x.mp4"), str(tmp_path / "y.mp4")]) == 1
    assert "Fehler" in capsys.readouterr().err
