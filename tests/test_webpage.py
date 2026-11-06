import pytest
from screenkaffee.webpage import capture_webpage, find_browser


def test_rejects_bad_scheme():
    with pytest.raises(ValueError):
        capture_webpage("javascript:alert(1)")


@pytest.mark.skipif(not find_browser(), reason="kein Chrome/Edge")
def test_full_page_capture(tmp_path):
    page = tmp_path / "p.html"
    page.write_text('<body style="margin:0"><div style="height:2500px;background:#00f"></div>'
                    '<div style="height:500px;background:#f00"></div></body>', encoding="utf-8")
    image = capture_webpage(page.as_uri(), width=600)
    assert image.width == 600 and 2950 <= image.height <= 3050
    assert image.getpixel((10, 10)) == (0, 0, 255)
    assert image.getpixel((10, image.height - 10)) == (255, 0, 0)
