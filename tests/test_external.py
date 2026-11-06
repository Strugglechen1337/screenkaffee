import sys
import pytest
from PIL import Image
from screenkaffee import external


def test_changed_and_export(tmp_path):
    path = external.export_temp(Image.new("RGB", (4, 4), "red"), tmp_path)
    mtime = path.stat().st_mtime_ns
    assert external.changed(path, mtime) is None
    Image.new("RGB", (4, 4), "blue").save(path)
    import os
    os.utime(path, ns=(mtime + 10**9, mtime + 10**9))
    assert external.changed(path, mtime) == mtime + 10**9
    assert external.changed(tmp_path / "nope.png", 0) is None


def test_launch_roundtrip_and_missing(tmp_path, qt_app):
    with pytest.raises(FileNotFoundError):
        external.launch(str(tmp_path / "nope.exe"), tmp_path / "x.png")
    from screenkaffee.editor import EditorWindow
    w = EditorWindow(Image.new("RGB", (8, 8), "red"))
    w.external_path = external.export_temp(Image.new("RGB", (8, 8), "red"), tmp_path)
    w.external_mtime = w.external_path.stat().st_mtime_ns
    Image.new("RGB", (8, 8), "blue").save(w.external_path)
    import os
    os.utime(w.external_path, ns=(w.external_mtime + 10**9,) * 2)
    w.check_external()
    assert w.view.source.getpixel((1, 1)) == (0, 0, 255)
