import subprocess
import pytest
from screenkaffee.media import (edit_video, export_html_page, ffmpeg_binary, probe_media, target_size,
                                OUTPUT_FORMATS, VIDEO_SIZES)


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    path = tmp_path_factory.mktemp("videos") / "quelle.mp4"
    subprocess.run([ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=d=1:s=320x180:r=15",
                    "-f", "lavfi", "-i", "sine=frequency=440:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(path)], check=True, timeout=120)
    return path


def test_size_modes_keep_the_aspect_ratio():
    assert target_size(622, 512, (960, 640), "fitmax") == (776, 640)
    assert target_size(622, 512, (960, 640), "max") == (622, 512)
    assert target_size(1920, 1080, (1280, 720), "max") == (1280, 720)
    assert target_size(622, 512, (960, 640), "exact") == (960, 640)
    with pytest.raises(ValueError):
        target_size(100, 100, (50, 50), "unbekannt")


def test_all_documented_formats_are_offered():
    for suffix in (".mp4", ".mkv", ".wmv", ".ogv", ".webm", ".gif", ".apng", ".webp", ".avi", ".mov"):
        assert suffix in OUTPUT_FORMATS
    assert ("Original", None) in VIDEO_SIZES


@pytest.mark.parametrize("suffix", [".mkv", ".wmv", ".ogv", ".webm", ".avi", ".mov", ".gif", ".apng", ".webp"])
def test_each_format_renders_a_playable_file(source, tmp_path, suffix):
    result = edit_video(source, tmp_path / ("ziel" + suffix), size=(160, 90), quality="low", fps=5)
    if suffix == ".webp":
        assert result.read_bytes()[:4] == b"RIFF" and result.read_bytes()[8:12] == b"WEBP"
        return
    info = probe_media(result)
    assert (info.width, info.height) == (160, 90)
    assert result.stat().st_size > 0
    assert info.has_audio is (suffix in (".mkv", ".wmv", ".ogv", ".webm", ".avi", ".mov"))


def test_exact_mode_pads_and_max_mode_never_enlarges(source, tmp_path):
    exact = probe_media(edit_video(source, tmp_path / "exakt.mp4", size=(400, 400), resize_mode="exact"))
    assert (exact.width, exact.height) == (400, 400)
    kept = probe_media(edit_video(source, tmp_path / "max.mp4", size=(1280, 720), resize_mode="max"))
    assert (kept.width, kept.height) == (320, 180)
    grown = probe_media(edit_video(source, tmp_path / "fit.mp4", size=(640, 360), resize_mode="fitmax"))
    assert (grown.width, grown.height) == (640, 360)


def test_quality_changes_the_file_size(source, tmp_path):
    low = edit_video(source, tmp_path / "klein.mp4", quality="low").stat().st_size
    high = edit_video(source, tmp_path / "gross.mp4", quality="high").stat().st_size
    assert high > low


def test_invalid_options_are_rejected(source, tmp_path):
    with pytest.raises(ValueError):
        edit_video(source, tmp_path / "x.mp4", quality="extrem")
    with pytest.raises(ValueError):
        edit_video(source, tmp_path / "x.mp4", fps=0)
    with pytest.raises(ValueError):
        edit_video(source, tmp_path / "x.flv")


def test_html5_page_references_a_playable_video(source, tmp_path):
    page = export_html_page(source, tmp_path / "seite.html")
    text = page.read_text(encoding="utf-8")
    video = tmp_path / "seite_dateien" / "quelle.mp4"
    assert "<video controls" in text and "seite_dateien/quelle.mp4" in text
    assert probe_media(video).width == 320
