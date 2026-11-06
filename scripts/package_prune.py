"""Hält die Pakete klein: nur die Qt-Teile und Python-Pakete, die screenkaffee wirklich nutzt.

Gemeinsam genutzt von package_windows.py (PyInstaller, x64), package_windows_arm64.py und package_linux.py.
Erst wird nach Namen aussortiert, dann wird jede verbliebene Qt-Datei auf ihre Qt-Abhängigkeiten geprüft
(PE-Importe bzw. ELF-DT_NEEDED): Plugins mit fehlenden Abhängigkeiten fliegen mit raus, fehlt einer
Kernbibliothek etwas, bricht der Build ab.
"""
from pathlib import Path, PurePosixPath
import re
import struct

# Von der App importierte Qt-Module (siehe grep "PySide6.Qt") plus ihre direkten Bausteine.
QT_MODULES = {"Core", "Gui", "Widgets", "Network", "DBus", "Multimedia", "MultimediaWidgets", "PrintSupport",
              "OpenGL", "OpenGLWidgets", "Svg", "SvgWidgets", "Xml", "Concurrent"}
# Nur als native Bibliothek nötig (Linux-Plattform-Plugins, FFmpeg-Stubs von QtMultimedia).
QT_NATIVE = QT_MODULES | {"XcbQpa", "WaylandClient", "WlShellIntegration", "EglFSDeviceIntegration",
                          "EglFsKmsSupport", "FFmpegStub-crypto", "FFmpegStub-ssl", "FFmpegStub-va",
                          "FFmpegStub-va-drm", "FFmpegStub-va-x11"}
# Unter Linux verlinkt das FFmpeg-Multimedia-Plugin zusätzlich Qt Quick/Qml (unter Windows nicht).
QT_NATIVE_LINUX = {"Quick", "Qml", "QmlMeta", "QmlModels", "QmlWorkerScript"}
PLUGIN_DIRS = {"generic", "iconengines", "imageformats", "multimedia", "networkinformation", "platforminputcontexts",
               "platforms", "platformthemes", "printsupport", "styles", "tls", "xcbglintegrations",
               "egldeviceintegrations", "wayland-decoration-client", "wayland-graphics-integration-client",
               "wayland-shell-integration"}
DROP_PLUGINS = {"qtvirtualkeyboardplugin", "qpdf"}
DROP_DIRS = {"qml", "translations", "metatypes", "include", "typesystems", "glue", "doc", "scripts", "lib", "libexec"}
DROP_FILES = {"opengl32sw.dll", "pyside6qml.abi3.dll", "pyside6qml.abi3.lib", "pyside6.abi3.lib"}
TOOLS = {"assistant", "balsam", "balsamui", "designer", "linguist", "lrelease", "lupdate", "lconvert", "qmlformat",
         "qmllint", "qmlls", "qsb", "svgtoqml", "qmlcachegen", "qmlimportscanner", "qmltyperegistrar", "rcc", "uic",
         "androiddeployqt", "qtpaths"}
# Werden von keinem genutzten Codepfad geladen: onnxruntime nur für Whispers VAD-Filter (vad_filter=False),
# hf_xet nur für Hugging-Face-Downloads (das Modell liegt lokal), flatbuffers/protobuf nur für onnxruntime.
DROP_PACKAGES = {"onnxruntime", "hf_xet", "flatbuffers", "google"}
DROP_DIST_INFO = re.compile(r"^(onnxruntime|hf_xet|flatbuffers|protobuf)-[^/]*\.dist-info$")

QT_LIBRARY = re.compile(r"^(?:lib)?Qt6([A-Za-z0-9-]+?)(?:\.dll|\.so(?:\.\d+)*)$")
QT_BINDING = re.compile(r"^Qt([A-Za-z0-9]+)\.(?:pyd|abi3\.so|pyi)$")
QT_DEPENDENCY = re.compile(r"^(?:lib)?(?:qt6|pyside6|shiboken6)", re.IGNORECASE)


def keep_pyside(relative):
    """relative: Pfad innerhalb des PySide6-Ordners (posix), z. B. 'Qt/lib/libQt6Core.so.6' oder 'plugins/tls/x.dll'."""
    parts = list(PurePosixPath(relative).parts)
    if parts and parts[0] == "Qt":               # Linux-Wheel: PySide6/Qt/{lib,plugins,qml,…}
        parts = parts[1:]
        if parts and parts[0] == "lib":          # Qt/lib/libQt6*.so wie die DLLs im Windows-Hauptordner behandeln
            parts = parts[1:]
    if not parts:
        return True
    name = parts[-1]
    if parts[0] in DROP_DIRS or name in DROP_FILES or name.endswith(".pyi") or name.endswith(".lib"):
        return False
    if parts[0] == "plugins":
        return len(parts) > 2 and parts[1] in PLUGIN_DIRS and not any(drop in name for drop in DROP_PLUGINS)
    if len(parts) == 1:
        stem = name[:-4] if name.endswith(".exe") else name
        if stem in TOOLS or name.endswith(".exe") or name.startswith("libpyside6qml"):
            return False
        if match := QT_BINDING.match(name):
            return match.group(1) in QT_MODULES
        if match := QT_LIBRARY.match(name):
            return match.group(1) in QT_NATIVE or (".so" in name and match.group(1) in QT_NATIVE_LINUX)
    return True


def keep_site(relative):
    """relative: Pfad innerhalb von site-packages (posix)."""
    parts = PurePosixPath(relative).parts
    if not parts:
        return True
    if parts[0] in DROP_PACKAGES or DROP_DIST_INFO.match(parts[0]):
        return False
    if parts[0] == "PySide6":
        return keep_pyside("/".join(parts[1:]))
    return True


def elf_needed(data):
    """DT_NEEDED-Einträge einer 64-Bit-ELF-Datei (None, wenn keine ELF-Datei)."""
    if data[:4] != b"\x7fELF" or data[4] != 2:
        return None
    order = "<" if data[5] == 1 else ">"
    shoff = struct.unpack_from(order + "Q", data, 0x28)[0]
    entsize, count = struct.unpack_from(order + "HH", data, 0x3A)
    sections = []
    for index in range(count):
        base = shoff + index * entsize
        kind = struct.unpack_from(order + "I", data, base + 4)[0]
        offset, size = struct.unpack_from(order + "QQ", data, base + 0x18)
        link = struct.unpack_from(order + "I", data, base + 0x28)[0]
        sections.append((kind, offset, size, link))
    needed = []
    for kind, offset, size, link in sections:
        if kind != 6:            # SHT_DYNAMIC
            continue
        strings = sections[link][1]
        for entry in range(offset, offset + size, 16):
            tag, value = struct.unpack_from(order + "qQ", data, entry)
            if tag == 0:
                break
            if tag == 1:         # DT_NEEDED
                end = data.index(b"\0", strings + value)
                needed.append(data[strings + value:end].decode())
    return needed


def pe_needed(path):
    import pefile
    pe = pefile.PE(str(path), fast_load=True)
    try:
        pe.parse_data_directories(directories=[1, 13])          # Importe und verzögerte Importe
        entries = getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) + getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])
        return [entry.dll.decode() for entry in entries]
    finally:
        pe.close()


def needed(path):
    path = Path(path)
    if path.suffix.lower() in (".dll", ".pyd", ".exe"):
        return pe_needed(path)
    if ".so" in path.name:
        with open(path, "rb") as handle:
            return elf_needed(handle.read()) or []
    return []


def drop_unresolved(files, root):
    """files: Pfade, die ins Paket sollen. Entfernt Plugins mit fehlenden Qt-Abhängigkeiten (bis nichts mehr fehlt).

    Liefert (behaltene Pfade, entfernte Pfade). Fehlt einer Nicht-Plugin-Datei eine Qt-Bibliothek: RuntimeError.
    Namen werden unter Windows ohne Groß-/Kleinschreibung verglichen."""
    root = Path(root)
    kept = [Path(p) for p in files]
    binaries = {p: [d for d in needed(p) if QT_DEPENDENCY.match(d)] for p in kept
                if "PySide6" in p.relative_to(root).parts and (p.suffix.lower() in (".dll", ".pyd") or ".so" in p.name)}
    removed = []
    while True:
        available = {p.name.lower() for p in kept}
        missing = {p: [d for d in deps if d.lower() not in available] for p, deps in binaries.items() if p in kept}
        missing = {p: deps for p, deps in missing.items() if deps}
        if not missing:
            return kept, removed
        for path, deps in missing.items():
            if "plugins" not in path.relative_to(root).parts:
                raise RuntimeError(f"{path.relative_to(root)} braucht {', '.join(deps)}, das aussortiert wurde.")
            kept.remove(path)
            removed.append(path)


def prune_tree(site):
    """Für PyInstaller (_internal) und entpackte Wheels: löscht Unnötiges direkt im Ordner. Liefert gelöschte Bytes."""
    site = Path(site)
    freed = 0
    files = []
    for path in site.rglob("*"):               # die Regeln gelten für Dateien, nie für ganze Ordner
        if not path.is_file():
            continue
        if keep_site(path.relative_to(site).as_posix()):
            files.append(path)
        else:
            freed += path.stat().st_size
            path.unlink()
    _, removed = drop_unresolved(files, site)
    for path in removed:
        freed += path.stat().st_size
        path.unlink()
    for folder in sorted((p for p in site.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()
    return freed
