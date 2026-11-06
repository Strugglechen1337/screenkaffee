from pathlib import Path
import os
import pefile
import sys

root = Path(sys.argv[1])
files = [p for p in root.rglob("*") if p.suffix.lower() in (".dll", ".pyd")]
paths = {}
for p in files:
    paths.setdefault(p.name.lower(), []).append(p)
cache = {}


def exports(path):
    if path not in cache:
        pe = pefile.PE(str(path), fast_load=True, max_symbol_exports=100_000)
        pe.parse_data_directories(directories=[0])
        symbols = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
        cache[path] = ({s.name for s in symbols.symbols}, {s.ordinal for s in symbols.symbols}) if symbols else (set(), set())
        pe.close()
    return cache[path]


for path in files:
    if not (path.parent.name in ("PySide6", "shiboken6", "winrt") or path.name.startswith("python")):
        continue
    pe = pefile.PE(str(path), fast_load=True)
    pe.parse_data_directories(directories=[1])
    for imp in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
        name = imp.dll.decode().lower()
        if name.startswith(("api-", "ext-")):
            continue
        matches = paths.get(name) or [Path(os.environ["SYSTEMROOT"]) / "System32" / name]
        for match in matches:
            if not match.exists():
                print("MISSING DLL", path.name, name)
                continue
            names, ordinals = exports(match)
            missing = [s.name or s.ordinal for s in imp.imports if (s.name is not None and s.name not in names) or (s.name is None and s.ordinal not in ordinals)]
            if missing:
                print("MISSING SYMBOL", path.name, "->", match, missing[:8])
    pe.close()
