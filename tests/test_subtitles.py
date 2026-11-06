import pytest
from screenkaffee.subtitles import Subtitle, read_srt, write_srt, validate


def test_srt_preserves_timing_multiline_and_unicode(tmp_path):
    captions = [Subtitle(.125, 1.625, "Grüße aus screenkaffee"), Subtitle(2, 4.125, "Eine Zeile\nEine zweite Zeile")]
    path = tmp_path / "test.srt"
    write_srt(captions, path)
    assert read_srt(path) == captions


def test_invalid_caption_timing_is_not_saved(tmp_path):
    path = tmp_path / "test.srt"
    path.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError):
        write_srt([Subtitle(2, 1, "Ungültig")], path)
    assert path.read_text(encoding="utf-8") == "keep"
