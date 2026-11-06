"""Ausdünnen der Pakete (scripts/package_prune.py)."""
from pathlib import Path
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import package_prune as prune  # noqa: E402


@pytest.mark.parametrize("path,keep", [
    ("PySide6/Qt6Core.dll", True), ("PySide6/QtMultimediaWidgets.pyd", True), ("PySide6/opengl32sw.dll", False),
    ("PySide6/Qt6WebEngineCore.dll", False), ("PySide6/Qt6Quick.dll", False), ("PySide6/Qt63DCore.dll", False),
    ("PySide6/QtQuick.pyd", False), ("PySide6/QtCore.pyi", False), ("PySide6/designer.exe", False),
    ("PySide6/translations/qtbase_de.qm", False), ("PySide6/qml/QtQuick/qmldir", False),
    ("PySide6/plugins/platforms/qwindows.dll", True), ("PySide6/plugins/imageformats/qsvg.dll", True),
    ("PySide6/plugins/imageformats/qpdf.dll", False), ("PySide6/plugins/sqldrivers/qsqlite.dll", False),
    ("PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll", False),
    ("PySide6/Qt/lib/libQt6Core.so.6", True), ("PySide6/Qt/lib/libQt6Quick.so.6", True),       # Linux: für FFmpeg-Plugin
    ("PySide6/Qt/lib/libQt6Quick3D.so.6", False), ("PySide6/Qt/lib/libavcodec.so.63", True),
    ("PySide6/Qt/plugins/platforminputcontexts/libcomposeplatforminputcontextplugin.so", True),
    ("PySide6/Qt/libexec/QtWebEngineProcess", False), ("PySide6/lib/cmake/x.cmake", False), ("PySide6/qmlls", False),
    ("PySide6/__init__.py", True), ("shiboken6/shiboken6.abi3.dll", True),
    ("onnxruntime/capi/onnxruntime.dll", False), ("onnxruntime-1.31.1.dist-info/METADATA", False),
    ("hf_xet/hf_xet.pyd", False), ("google/protobuf/__init__.py", False), ("faster_whisper/vad.py", True),
    ("ctranslate2/__init__.py", True), ("numpy/__init__.py", True),
])
def test_keep_rules(path, keep):
    assert prune.keep_site(path) is keep


def test_prune_tree_keeps_plugin_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(prune, "needed", lambda path: [])
    pyside = tmp_path / "PySide6"
    for name in ["Qt6Core.dll", "Qt6Quick.dll", "plugins/platforms/qwindows.dll", "plugins/qmltooling/x.dll",
                 "translations/qtbase_de.qm", "onnxruntime/capi/a.dll"]:
        target = (tmp_path / "onnxruntime/capi/a.dll") if name.startswith("onnx") else pyside / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
    assert prune.prune_tree(tmp_path) == 4
    assert (pyside / "plugins" / "platforms" / "qwindows.dll").is_file() and (pyside / "Qt6Core.dll").is_file()
    assert not (pyside / "plugins" / "qmltooling").exists() and not (pyside / "translations").exists()
    assert not (tmp_path / "onnxruntime").exists()


def tiny_elf(needed):
    """64-Bit-ELF mit .dynstr und .dynamic (nur Section-Header, genug für DT_NEEDED)."""
    strings = b"\0" + b"".join(name.encode() + b"\0" for name in needed)
    offsets, position = [], 1
    for name in needed:
        offsets.append(position)
        position += len(name) + 1
    dynamic = b"".join(struct.pack("<qQ", 1, offset) for offset in offsets) + struct.pack("<qQ", 0, 0)
    header_size = 64
    strings_at, dynamic_at = header_size, header_size + len(strings)
    sections_at = dynamic_at + len(dynamic)
    def section(kind, offset, size, link):
        return struct.pack("<IIQQQQIIQQ", 0, kind, 0, 0, offset, size, link, 0, 1, 0)
    sections = section(0, 0, 0, 0) + section(3, strings_at, len(strings), 0) + section(6, dynamic_at, len(dynamic), 1)
    header = bytearray(header_size)
    header[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<Q", header, 0x28, sections_at)
    struct.pack_into("<HHH", header, 0x3A, 64, 3, 0)
    return bytes(header) + strings + dynamic + sections


def test_elf_dependencies_are_read():
    assert prune.elf_needed(tiny_elf(["libQt6Quick.so.6", "libc.so.6"])) == ["libQt6Quick.so.6", "libc.so.6"]
    assert prune.elf_needed(b"MZ not an elf") is None


def test_plugins_with_missing_qt_libraries_are_dropped(tmp_path):
    qt = tmp_path / "PySide6" / "Qt"
    (qt / "lib").mkdir(parents=True)
    (qt / "plugins" / "multimedia").mkdir(parents=True)
    files = {qt / "lib" / "libQt6Core.so.6": [], qt / "lib" / "libQt6Multimedia.so.6": ["libQt6Core.so.6"],
             qt / "plugins" / "multimedia" / "libgood.so": ["libQt6Multimedia.so.6", "libc.so.6"],
             qt / "plugins" / "multimedia" / "libbad.so": ["libQt6Quick3D.so.6"]}
    for path, deps in files.items():
        path.write_bytes(tiny_elf(deps))
    kept, removed = prune.drop_unresolved(list(files), tmp_path)
    assert [p.name for p in removed] == ["libbad.so"] and len(kept) == 3
    (qt / "lib" / "libQt6Multimedia.so.6").write_bytes(tiny_elf(["libQt6Quick3D.so.6"]))
    with pytest.raises(RuntimeError, match="Quick3D"):
        prune.drop_unresolved(list(files), tmp_path)
