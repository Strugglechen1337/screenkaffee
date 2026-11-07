"""Windows Graphics Capture in FP16, with native DXGI/D3D11 GPU readback.

COM method indices are from the Windows SDK interfaces. Interfaces and acquired
frames have deterministic lifetimes. HDR never silently falls back to BGRA8.
"""
import logging
import ctypes as c
from ctypes import wintypes as w
from dataclasses import dataclass
import time
import uuid
import numpy as np
from PIL import Image
from .color import scrgb_to_srgb
from .i18n import tr

U = c.c_uint32
I = c.c_int32
P = c.c_void_p
HR = c.c_int32


LOG = logging.getLogger(__name__)


class GUID(c.Structure):
    _fields_ = [("raw", c.c_ubyte * 16)]
    def __init__(self, text):
        super().__init__()
        self.raw[:] = uuid.UUID(text).bytes_le


class LUID(c.Structure):
    _fields_ = [("low", U), ("high", I)]


class RECT(c.Structure):
    _fields_ = [("left", I), ("top", I), ("right", I), ("bottom", I)]


class OutputDesc(c.Structure):
    _fields_ = [("name", c.c_wchar * 32), ("rect", RECT), ("attached", I),
                ("rotation", U), ("monitor", P)]


class OutputDesc1(c.Structure):
    _fields_ = OutputDesc._fields_ + [("bits", U), ("color_space", U),
        ("red", c.c_float * 2), ("green", c.c_float * 2), ("blue", c.c_float * 2),
        ("white", c.c_float * 2), ("min_nits", c.c_float),
        ("max_nits", c.c_float), ("max_full_nits", c.c_float)]


class TextureDesc(c.Structure):
    _fields_ = [(name, U) for name in ("width", "height", "mips", "array", "format",
        "sample_count", "sample_quality", "usage", "bind", "cpu", "misc")]


class Mapped(c.Structure):
    _fields_ = [("data", P), ("row_pitch", U), ("depth_pitch", U)]


class FrameInfo(c.Structure):
    _fields_ = [("present", c.c_int64), ("mouse", c.c_int64), ("frames", U),
                ("coalesced", I), ("protected", I), ("pointer_x", I),
                ("pointer_y", I), ("visible", I), ("metadata", U), ("shape_size", U)]


class SourcePath(c.Structure):
    _fields_ = [("adapter", LUID), ("id", U), ("mode", U), ("status", U)]


class TargetPath(c.Structure):
    _fields_ = [("adapter", LUID), ("id", U), ("mode", U), ("technology", U),
        ("rotation", U), ("scaling", U), ("refresh_num", U), ("refresh_den", U),
        ("scanline", U), ("available", I), ("status", U)]


class DisplayPath(c.Structure):
    _fields_ = [("source", SourcePath), ("target", TargetPath), ("flags", U)]


class DeviceHeader(c.Structure):
    _fields_ = [("type", U), ("size", U), ("adapter", LUID), ("id", U)]


class SourceName(c.Structure):
    _fields_ = [("header", DeviceHeader), ("name", c.c_wchar * 32)]


class WhiteLevel(c.Structure):
    _fields_ = [("header", DeviceHeader), ("value", U)]


def check(hr, context):
    if hr < 0:
        raise RuntimeError(f"{context}: HRESULT 0x{hr & 0xffffffff:08X}")


class Com:
    def __init__(self, ptr=None):
        self.ptr = ptr if isinstance(ptr, P) else P(ptr)

    def call(self, slot, restype, argtypes, *args):
        table = c.cast(self.ptr, c.POINTER(c.POINTER(P))).contents
        method = c.WINFUNCTYPE(restype, P, *argtypes)(table[slot])
        return method(self.ptr, *args)

    def query(self, iid):
        out = P()
        check(self.call(0, HR, [c.POINTER(GUID), c.POINTER(P)], c.byref(GUID(iid)), c.byref(out)), "QueryInterface")
        return Com(out)

    def close(self):
        if self.ptr:
            self.call(2, U, [])
            self.ptr = P()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def sdr_white_level(device_name):
    api = c.WinDLL("user32")
    paths_count, modes_count = U(), U()
    for _ in range(3):
        if api.GetDisplayConfigBufferSizes(2, c.byref(paths_count), c.byref(modes_count)):
            break
        paths = (DisplayPath * paths_count.value)()
        # DISPLAYCONFIG_MODE_INFO is 64 bytes, with 8-byte alignment.
        modes = (c.c_uint64 * (modes_count.value * 8))()
        result = api.QueryDisplayConfig(2, c.byref(paths_count), paths,
                                        c.byref(modes_count), modes, None)
        if result == 122:
            continue
        if result:
            break
        for path in paths[:paths_count.value]:
            source = SourceName(DeviceHeader(1, c.sizeof(SourceName), path.source.adapter, path.source.id))
            if api.DisplayConfigGetDeviceInfo(c.byref(source)) or source.name != device_name:
                continue
            white = WhiteLevel(DeviceHeader(11, c.sizeof(WhiteLevel), path.target.adapter, path.target.id))
            if api.DisplayConfigGetDeviceInfo(c.byref(white)) == 0 and white.value:
                return white.value / 1000 * 80
    raise RuntimeError(tr("Windows-SDR-Weißpegel konnte nicht ermittelt werden. HDR-Aufnahme abgebrochen."))


@dataclass
class CapturedMonitor:
    name: str
    bounds: tuple
    image: Image.Image
    metadata: dict


def capture_monitors(mode="neutral", cursor=False, window=None, target=None):
    if c.sizeof(P) != 8:
        raise RuntimeError(tr("64-Bit-Windows erforderlich."))
    api = c.WinDLL("dxgi")
    api.CreateDXGIFactory1.argtypes = [c.POINTER(GUID), c.POINTER(P)]
    api.CreateDXGIFactory1.restype = HR
    factory_ptr = P()
    check(api.CreateDXGIFactory1(c.byref(GUID("770aae78-f26f-4dba-a829-253c83d1b387")), c.byref(factory_ptr)), tr("DXGI-Factory"))
    captures = []
    with Com(factory_ptr) as factory:
        index = 0
        while True:
            adapter_ptr = P()
            hr = factory.call(12, HR, [U, c.POINTER(P)], index, c.byref(adapter_ptr))
            if hr & 0xffffffff == 0x887A0002:
                break
            check(hr, tr("Grafikadapter"))
            with Com(adapter_ptr) as adapter:
                output_index = 0
                while True:
                    output_ptr = P()
                    hr = adapter.call(7, HR, [U, c.POINTER(P)], output_index, c.byref(output_ptr))
                    if hr & 0xffffffff == 0x887A0002:
                        break
                    check(hr, tr("Monitor"))
                    with Com(output_ptr) as output:
                        captured = _capture_wgc(adapter, output, mode, cursor, window, target=target)
                        if captured:
                            captures.append(captured)
                    output_index += 1
            index += 1
    if not captures and target is not None:
        return capture_monitors(mode, cursor, window, target=None)
    if not captures:
        raise RuntimeError(tr("Kein aktiver Monitor gefunden."))
    return captures


def _capture_wgc(adapter, output, mode, cursor=False, window=None, target=None):
    from winrt.windows.graphics.capture import Direct3D11CaptureFramePool, GraphicsCaptureSession
    from winrt.windows.graphics.capture.interop import create_for_monitor, create_for_window
    from winrt.windows.graphics.directx import DirectXPixelFormat
    from winrt.windows.graphics.directx.direct3d11.interop import (
        create_direct3d11_device_from_dxgi_device, get_dxgi_surface_from_object)
    if not GraphicsCaptureSession.is_supported():
        raise RuntimeError(tr("Windows Graphics Capture wird nicht unterstützt."))
    desc = OutputDesc()
    check(output.call(7, HR, [c.POINTER(OutputDesc)], c.byref(desc)), tr("Monitorbeschreibung"))
    if not desc.attached:
        return None
    if target is not None:
        if isinstance(target, str):
            if desc.name != target and not desc.name.endswith(target):
                return None
        elif isinstance(target, (tuple, list)) and len(target) == 4:
            tl, tt, tright, tb = map(int, target)
            if min(tright, desc.rect.right) <= max(tl, desc.rect.left) or min(tb, desc.rect.bottom) <= max(tt, desc.rect.top):
                return None
        elif isinstance(target, (tuple, list)) and len(target) >= 2:
            if (desc.rect.left, desc.rect.top) != (int(target[0]), int(target[1])):
                return None
    if window:
        user = c.WinDLL("user32")
        user.MonitorFromWindow.argtypes = [P, U]
        user.MonitorFromWindow.restype = P
        if desc.monitor != user.MonitorFromWindow(P(window), 2):
            return None
    with output.query("068346e8-aaec-4b84-add7-137f513f77a1") as output6:
        advanced = OutputDesc1()
        check(output6.call(27, HR, [c.POINTER(OutputDesc1)], c.byref(advanced)), tr("HDR-Monitorbeschreibung"))
    hdr = advanced.color_space == 12
    white = sdr_white_level(desc.name) if hdr else 80.
    device_ptr, context_ptr, feature = P(), P(), U()
    api = c.WinDLL("d3d11")
    api.D3D11CreateDevice.argtypes = [P, U, P, U, P, U, U, c.POINTER(P), c.POINTER(U), c.POINTER(P)]
    api.D3D11CreateDevice.restype = HR
    check(api.D3D11CreateDevice(adapter.ptr, 0, None, 0x20, None, 0, 7,
        c.byref(device_ptr), c.byref(feature), c.byref(context_ptr)), tr("WGC D3D11CreateDevice"))
    with Com(device_ptr) as device, Com(context_ptr) as context, \
         device.query("54ec77fa-1377-44e6-8c32-88fd5f44c84c") as dxgi_device:
        rt_device = create_direct3d11_device_from_dxgi_device(dxgi_device.ptr.value)
        item = create_for_window(window) if window else create_for_monitor(desc.monitor)
        # FP16 throughout, including SDR monitors; WGC supplies linear scRGB.
        pool = Direct3D11CaptureFramePool.create_free_threaded(rt_device,
            DirectXPixelFormat.R16_G16_B16_A16_FLOAT, 1, item.size)
        session = pool.create_capture_session(item)
        frame = None
        try:
            try:
                session.is_border_required = False
            except Exception:
                LOG.debug("is_border_required wird nicht unterstützt (älteres Windows)", exc_info=True)
            session.is_cursor_capture_enabled = cursor
            session.start_capture()
            deadline = time.monotonic() + 5
            while frame is None and time.monotonic() < deadline:
                frame = pool.try_get_next_frame()
                if frame is None:
                    time.sleep(.001)
            if frame is None:
                raise RuntimeError(tr("Kein WGC-Frame innerhalb von 5 Sekunden erhalten."))
            with Com(get_dxgi_surface_from_object(frame.surface)) as surface, \
                 surface.query("6f15aaf2-d208-4e89-9ab4-489535d34f9c") as texture:
                td = TextureDesc()
                texture.call(10, None, [c.POINTER(TextureDesc)], c.byref(td))
                if td.format != 10:
                    raise RuntimeError(tr("WGC lieferte kein FP16-scRGB."))
                td.usage, td.bind, td.cpu, td.misc = 3, 0, 0x20000, 0
                staging_ptr = P()
                check(device.call(5, HR, [c.POINTER(TextureDesc), P, c.POINTER(P)],
                    c.byref(td), None, c.byref(staging_ptr)), tr("WGC Staging"))
                with Com(staging_ptr) as staging:
                    context.call(47, None, [P, P], staging.ptr, texture.ptr)
                    mapped = Mapped()
                    check(context.call(14, HR, [P, U, U, U, c.POINTER(Mapped)],
                        staging.ptr, 0, 1, 0, c.byref(mapped)), tr("WGC GPU-Lesezugriff"))
                    try:
                        raw = c.string_at(mapped.data, mapped.row_pitch * td.height)
                    finally:
                        context.call(15, None, [P, U], staging.ptr, 0)
                width, height = frame.content_size.width, frame.content_size.height
                if width > td.width or height > td.height or min(width, height) < 1:
                    raise RuntimeError(tr("Aufnahmeziel wurde während der Aufnahme geändert. Bitte erneut aufnehmen."))
                rgb = np.ndarray((height, width, 3), dtype=np.float16, buffer=raw, strides=(mapped.row_pitch, 8, 2))
                image = Image.fromarray(scrgb_to_srgb(rgb, white, mode))
        finally:
            if frame is not None:
                frame.close()
            session.close()
            pool.close()
            rt_device.close()
            del frame, session, pool, item, rt_device
    r = desc.rect
    if window:
        r = RECT()
        dwm = c.WinDLL("dwmapi")
        dwm.DwmGetWindowAttribute.argtypes = [P, U, P, U]
        check(dwm.DwmGetWindowAttribute(P(window), 9, c.byref(r), c.sizeof(r)), tr("Fenstergeometrie"))
        r.right, r.bottom = r.left + image.width, r.top + image.height
    elif image.size != (r.right - r.left, r.bottom - r.top):
        raise RuntimeError(tr("Monitorgröße hat sich während der Aufnahme geändert."))
    return CapturedMonitor(desc.name, (r.left, r.top, r.right, r.bottom), image,
        {"backend": "Windows Graphics Capture" + (" · Fenster" if window else ""), "hdr": hdr, "sdr_white_nits": white,
         "source_format": "scRGB FP16", "tone_mapping": mode, "display": desc.name,
         "desktop_bounds": [r.left, r.top, r.right, r.bottom]})


def warmup_capture():
    """Preload WinRT capture bindings and the SDR FP16->sRGB lookup table."""
    from .color import _f16_neutral_lut
    _f16_neutral_lut(80.)
    from winrt.windows.graphics.capture import Direct3D11CaptureFramePool, GraphicsCaptureSession  # noqa: F401
    from winrt.windows.graphics.capture.interop import create_for_monitor, create_for_window  # noqa: F401
    from winrt.windows.graphics.directx import DirectXPixelFormat  # noqa: F401
    from winrt.windows.graphics.directx.direct3d11.interop import (  # noqa: F401
        create_direct3d11_device_from_dxgi_device, get_dxgi_surface_from_object)


def capture_window(handle, mode="neutral", cursor=False):
    return capture_monitors(mode, cursor, window=handle)[0]


def window_rectangles(include_handles=False):
    """Visible top-level windows in z-order, excluding our own process."""
    import os
    user = c.WinDLL("user32")
    user.IsWindowVisible.argtypes = [P]
    user.GetWindowTextLengthW.argtypes = [P]
    user.GetWindowTextW.argtypes = [P, c.c_wchar_p, I]
    user.GetWindowThreadProcessId.argtypes = [P, c.POINTER(U)]
    user.GetWindowRect.argtypes = [P, c.POINTER(RECT)]
    dwm = c.WinDLL("dwmapi")
    dwm.DwmGetWindowAttribute.argtypes = [P, U, P, U]
    windows = []
    callback_type = c.WINFUNCTYPE(I, P, P)

    @callback_type
    def callback(hwnd, parameter):
        if not user.IsWindowVisible(hwnd):
            return 1
        process = U()
        user.GetWindowThreadProcessId(hwnd, c.byref(process))
        if process.value == os.getpid():
            return 1
        cloaked = U()
        if dwm.DwmGetWindowAttribute(hwnd, 14, c.byref(cloaked), c.sizeof(cloaked)) == 0 and cloaked.value:
            return 1
        length = user.GetWindowTextLengthW(hwnd)
        if not length:
            return 1
        title = c.create_unicode_buffer(length + 1)
        user.GetWindowTextW(hwnd, title, length + 1)
        rect = RECT()
        if dwm.DwmGetWindowAttribute(hwnd, 9, c.byref(rect), c.sizeof(rect)) != 0:
            user.GetWindowRect(hwnd, c.byref(rect))
        if rect.right > rect.left and rect.bottom > rect.top:
            entry = (title.value, (rect.left, rect.top, rect.right, rect.bottom))
            windows.append((*entry, hwnd) if include_handles else entry)
        return 1

    user.EnumWindows.argtypes = [callback_type, P]
    user.EnumWindows(callback, None)
    return windows
