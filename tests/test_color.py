import numpy as np
import pytest
from PIL import Image
from screenkaffee.color import scrgb_to_srgb, save_png, import_srgb


def test_sdr_white_boost_is_removed_before_gamma():
    linear = np.array([[[0, .18, 1], [.003, .5, .8]]], dtype=np.float32)
    reference = scrgb_to_srgb(linear, 80)
    np.testing.assert_array_equal(reference, scrgb_to_srgb(linear * 3, 240))
    assert tuple(reference[0, 0]) == (0, 118, 255)


def test_sdr_roundtrip_is_exact_for_all_8bit_values():
    encoded = np.arange(256, dtype=np.float32) / 255
    linear = np.where(encoded <= .04045, encoded / 12.92, ((encoded + .055) / 1.055) ** 2.4)
    rgb = np.repeat(linear.reshape(1, 256, 1), 3, axis=2)
    recovered = scrgb_to_srgb(rgb * 2.5, 200)
    np.testing.assert_array_equal(recovered[0, :, 0], np.arange(256, dtype=np.uint8))


def test_highlights_monotone_and_content_independent():
    values = np.array([0, .2, .8, 1, 2, 5, 20], dtype=np.float32)
    rgb = np.repeat(values.reshape(1, -1, 1), 3, axis=2)
    output = scrgb_to_srgb(rgb, 80, "highlights")
    assert np.all(np.diff(output[0, :, 0].astype(int)) >= 0)
    assert output[0, 0, 0] == 0
    assert output[0, -1, 0] == 255
    np.testing.assert_array_equal(output[:, :3], scrgb_to_srgb(rgb[:, :3], 80, "highlights"))


def test_nonfinite_and_invalid_white():
    output = scrgb_to_srgb(np.array([[[np.nan, -2, np.inf]]]), 80)
    assert tuple(output[0, 0]) == (0, 0, 255)
    for white in (0, -1, np.nan, 10000):
        with pytest.raises(ValueError):
            scrgb_to_srgb(np.zeros((1, 1, 3)), white)


def test_png_is_profiled_and_roundtrips(tmp_path):
    path = tmp_path / "colors.png"
    save_png(Image.new("RGB", (8, 8), (44, 88, 132)), path, {"sdr_white_nits": 200})
    with Image.open(path) as image:
        assert image.info["icc_profile"]
        assert image.info["sdr_white_nits"] == "200"
    assert import_srgb(path).getpixel((0, 0)) == (44, 88, 132)


def test_float16_lut_matches_float32_reference():
    sample_f16 = np.array([[[0.0, 0.18, 1.0], [0.003, 0.5, 2.5], [np.nan, -1.0, np.inf]]], dtype=np.float16)
    for white in (80.0, 200.0):
        for mode in ("neutral", "highlights"):
            fast = scrgb_to_srgb(sample_f16, white, mode)
            ref = scrgb_to_srgb(sample_f16.astype(np.float32), white, mode)
            np.testing.assert_array_equal(fast, ref)

