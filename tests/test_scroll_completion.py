import numpy as np
import pytest
from PIL import Image
from screenkaffee.stitch import stitch_scroll
from screenkaffee.scroll_session import ManualScrollSession
from screenkaffee.capture import scroll_capture


@pytest.mark.parametrize("direction", ["vertical", "horizontal"])
def test_fixed_bars_live_series_and_auto_scrolling(tmp_path, direction):
    page = Image.fromarray(np.random.default_rng(7).integers(0, 255, (240, 80, 3), dtype=np.uint8))
    frames = []
    for offset in (0, 60, 120):
        frame = Image.new("RGB", (80, 152), "black")
        frame.paste("purple", (0, 0, 80, 20))
        frame.paste(page.crop((0, offset, 80, offset + 120)), (0, 20))
        frames.append(frame)
    expected = Image.new("RGB", (80, 272), "black")
    expected.paste("purple", (0, 0, 80, 20))
    expected.paste(page, (0, 20))
    if direction == "horizontal":
        frames = [frame.transpose(Image.Transpose.TRANSPOSE) for frame in frames]
        expected = expected.transpose(Image.Transpose.TRANSPOSE)
    options = dict(direction=direction, header=20, footer=12)
    combined = stitch_scroll(frames, **options)
    assert combined.size == expected.size and combined.tobytes() == expected.tobytes()
    session = ManualScrollSession(tmp_path, (0, 0, *frames[0].size), **options)
    for frame in frames:
        session.append(frame)
    assert len(session.paths) == 3
    assert (session.folder / "recovery.json").exists()
    assert session.finish().tobytes() == expected.tobytes()
    iterator = iter([*frames, frames[-1]])
    class NoWait:
        def is_set(self):
            return False
        def wait(self, seconds):
            return False
    result = scroll_capture((0, 0, *frames[0].size), capture=lambda: next(iterator), scroll=lambda: None,
                            cancel=NoWait(), **options)
    assert result.image.tobytes() == expected.tobytes()
    assert result.frames == 3
    assert "Ende" in result.stop_reason
