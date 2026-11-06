import threading
import numpy as np
from PIL import Image
from screenkaffee.capture import scroll_capture


def test_automatic_scroll_capture_reconstructs_page_and_detects_end():
    rng = np.random.default_rng(11)
    page = Image.fromarray(rng.integers(0, 256, (900, 200, 3), dtype=np.uint8))
    frames = [page.crop((0, y, 200, y + 400)) for y in (0, 250, 500, 500)]
    index = [0]
    def capture():
        return frames[index[0]]
    def scroll():
        index[0] = min(index[0] + 1, len(frames) - 1)
    result = scroll_capture((0, 0, 200, 400), pause=.1, capture=capture, scroll=scroll)
    assert result.image.tobytes() == page.tobytes()
    assert result.frames == 3
    assert "Ende" in result.stop_reason


def test_scroll_cancellation_keeps_captured_content():
    event = threading.Event()
    event.set()
    result = scroll_capture((0, 0, 100, 100), cancel=event,
        capture=lambda: Image.new("RGB", (100, 100), "red"), scroll=lambda: None)
    assert result.frames == 1
    assert result.image.getpixel((0, 0)) == (255, 0, 0)
    assert "Nutzer" in result.stop_reason
