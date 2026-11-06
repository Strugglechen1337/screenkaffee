import pytest
from PIL import Image
from screenkaffee.color import JXL_AVAILABLE, import_srgb, save_image
from screenkaffee.batch import export_any, export_batch, split_title_extension

pytestmark = pytest.mark.skipif(not JXL_AVAILABLE, reason="pillow-jxl-plugin nicht installiert")


def test_jpeg_xl_round_trip_keeps_the_picture(tmp_path):
    image = Image.new("RGB", (80, 60), "#3a78c2")
    path = tmp_path / "bild.jxl"
    save_image(image, path, quality=95)
    assert path.read_bytes()[:2] in (b"\xff\x0a", b"\x00\x00")           # JXL codestream or ISO-BMFF container
    loaded = import_srgb(path)
    assert loaded.size == (80, 60)
    r, g, b = loaded.convert("RGB").getpixel((40, 30))
    assert abs(r - 0x3a) <= 4 and abs(g - 0x78) <= 4 and abs(b - 0xc2) <= 4


def test_jpeg_xl_keeps_transparency(tmp_path):
    image = Image.new("RGBA", (16, 16), (0, 255, 0, 100))
    save_image(image, tmp_path / "alpha.jxl")
    assert 90 <= import_srgb(tmp_path / "alpha.jxl").getpixel((3, 3))[3] <= 110


def test_jpeg_xl_in_save_as_batch_export_and_title_extension(tmp_path):
    image = Image.new("RGB", (50, 40), "white")
    assert export_any(image, "Titel", tmp_path / "a.jxl").exists()
    paths = export_batch([("Eins", image)], tmp_path / "aus", ".jxl", {"scale_percent": 50})
    assert [p.name for p in paths] == ["Eins.jxl"] and import_srgb(paths[0]).size == (25, 20)
    assert split_title_extension("Foto.JXL") == ("Foto", ".jxl")


def test_unknown_suffixes_are_still_rejected(tmp_path):
    with pytest.raises(ValueError):
        save_image(Image.new("RGB", (4, 4)), tmp_path / "x.xyz")
