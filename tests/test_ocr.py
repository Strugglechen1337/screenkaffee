import importlib.util
from pathlib import Path
import pytest
from PIL import Image, ImageDraw, ImageFont
from screenkaffee.ocr import recognize, sensitive_regions, Recognition, Word

FIXTURE_FONT = Path(__file__).parent / "fixtures" / "DejaVuSans.ttf"


@pytest.mark.skipif(not FIXTURE_FONT.is_file() or not (Path(__file__).resolve().parent.parent / "models" / "tessdata").is_dir()
                    or importlib.util.find_spec("tesserocr") is None,
                    reason="Texterkennung ist aus der App entfernt; braucht tesserocr, DejaVuSans.ttf und models/tessdata")
def test_bundled_german_english_ocr_reads_real_pixels():
    image = Image.new("RGB", (1000, 210), "white")
    font = ImageFont.truetype(str(FIXTURE_FONT), 38)
    draw = ImageDraw.Draw(image)
    draw.text((30, 30), "Hallo screenkaffee", fill="black", font=font)
    draw.text((30, 100), "Kontakt: test@example.com", fill="black", font=font)
    result = recognize(image)
    assert "screenkaffee" in result.text
    assert "test@example.com" in result.text
    regions = sensitive_regions(result)
    assert regions
    assert any(word.text == "test@example.com" and word.box[0] > 100 for word in result.words)


def test_sensitive_detection_does_not_join_separate_lines():
    result = Recognition("", [Word("test", (0, 0, 30, 20), 90),
        Word("@example.com", (0, 50, 120, 70), 90)])
    assert not sensitive_regions(result)
