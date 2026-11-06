"""Opt-in real X11 CLI capture against an isolated Xvfb display."""
import ctypes
import os
import subprocess
import sys

import pytest
from PIL import Image

pytestmark = pytest.mark.skipif(not os.environ.get("SCREENKAFFEE_TEST_X11"), reason="requires private X11 display")


def draw_pattern(env):
    subprocess.run(["xsetroot", "-solid", "#d02040"], env=env, check=True)
    x = ctypes.CDLL("libX11.so.6")
    x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XOpenDisplay.restype = ctypes.c_void_p
    x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x.XDefaultRootWindow.restype = ctypes.c_ulong
    x.XCreateGC.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
    x.XCreateGC.restype = ctypes.c_void_p
    x.XSetForeground.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    x.XFillRectangle.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    x.XFreeGC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    x.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = x.XOpenDisplay(env["DISPLAY"].encode())
    assert display
    root = x.XDefaultRootWindow(display)
    gc = x.XCreateGC(display, root, 0, None)
    x.XSetForeground(display, gc, 0x20d040)
    x.XFillRectangle(display, root, gc, 50, 60, 30, 20)
    x.XFreeGC(display, gc)
    x.XCloseDisplay(display)


def cli(env, target, rect):
    executable = env.get("SCREENKAFFEE_TEST_PACKAGE")
    command = [executable] if executable else [sys.executable, "-m", "screenkaffee"]
    return subprocess.run([*command, "capture", "--screenshotregion", "--rect", rect, "--filepath", str(target)], env=env, capture_output=True, text=True, timeout=30)


def test_cli_fixed_region_captures_x11_pixels(tmp_path):
    env = os.environ.copy()
    env.update(QT_QPA_PLATFORM="xcb", XDG_SESSION_TYPE="x11")
    env.pop("WAYLAND_DISPLAY", None)
    draw_pattern(env)
    target = tmp_path / "region.png"
    result = cli(env, target, "30;40;70;60")
    assert result.returncode == 0, result.stderr
    with Image.open(target) as im:
        im = im.convert("RGB")
        assert im.size == (70, 60)
        assert im.getpixel((0, 0)) == (208, 32, 64)
        assert im.getpixel((20, 20)) == (32, 208, 64)
        assert im.getpixel((49, 39)) == (32, 208, 64)
        assert im.getpixel((50, 40)) == (208, 32, 64)


def test_cli_outside_screen_fails_without_file(tmp_path):
    env = os.environ.copy()
    env.update(QT_QPA_PLATFORM="xcb", XDG_SESSION_TYPE="x11")
    env.pop("WAYLAND_DISPLAY", None)
    target = tmp_path / "outside.png"
    result = cli(env, target, "900;700;20;20")
    assert result.returncode != 0
    assert not target.exists()


def test_cli_partial_offscreen_is_black_padded(tmp_path):
    env = os.environ.copy()
    env.update(QT_QPA_PLATFORM="xcb", XDG_SESSION_TYPE="x11")
    env.pop("WAYLAND_DISPLAY", None)
    draw_pattern(env)
    target = tmp_path / "partial.png"
    result = cli(env, target, "-10;-10;30;30")
    assert result.returncode == 0, result.stderr
    with Image.open(target) as image:
        assert image.size == (30, 30)
        assert image.convert("RGB").getpixel((0, 0)) == (0, 0, 0)
        assert image.convert("RGB").getpixel((15, 15)) == (208, 32, 64)


def test_cli_scaled_x11_rejects_without_misleading_capture(tmp_path):
    env = os.environ.copy()
    env.update(QT_QPA_PLATFORM="xcb", XDG_SESSION_TYPE="x11", QT_SCALE_FACTOR="2")
    env.pop("WAYLAND_DISPLAY", None)
    target = tmp_path / "scaled.png"
    result = cli(env, target, "30;40;70;60")
    assert result.returncode != 0
    assert "skalierung" in result.stderr.lower()
    assert not target.exists()
