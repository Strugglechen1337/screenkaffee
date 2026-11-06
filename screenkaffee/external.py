"""Round trip through an external image editor via a temporary PNG."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def export_temp(image, folder=None):
    folder = Path(folder or tempfile.mkdtemp(prefix="screenkaffee-extern-"))
    path = folder / "bearbeiten.png"
    image.save(path)
    return path


def launch(program, path):
    """Start the chosen program with the file; no shell is involved."""
    if not Path(program).is_file():
        raise FileNotFoundError("Programm nicht gefunden: " + program)
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen([program, str(path)], creationflags=flags)


def changed(path, last_mtime):
    """Return the new mtime if the file was modified since last_mtime, else None."""
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        return None
    return mtime if mtime != last_mtime else None
