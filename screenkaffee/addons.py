"""Optionale Erweiterungen, die nicht im Programmpaket stecken – bisher die lokale Untertitel-Erkennung (Whisper).

Das Paket bringt eine ``addons.json`` mit: welches Release-Asset zur Erweiterung gehört und deren Fingerabdruck
(SHA256 über Dateiliste und Inhalte). Die Erweiterung wird einmal aus demselben GitHub-Release wie die App geladen,
per SHA256 geprüft und unter ``data/addons/speech-<Fingerabdruck>`` abgelegt. Von dort werden ihre Dateien in den
App-Ordner verlinkt (Hardlink, sonst Kopie). Nach einem Update fehlen sie dort wieder; passt der Fingerabdruck noch,
werden sie ohne neuen Download wieder verlinkt.
"""
import json
import os
from pathlib import Path
import shutil
import zipfile

from . import updater
from .paths import application_root

INFO = "addons.json"                  # im App-Ordner, vom Paketbau geschrieben
MARKER = "speech-addon.json"          # im App-Ordner, nach dem Verlinken
LONG_PREFIX = "\\\\?\\"
SPEECH_SIZE_HINT = "ca. 190 MB Download (Linux 220 MB), gut 300 MB auf der Platte"


class AddonError(RuntimeError):
    pass


def package_info(root=None):
    path = Path(root or application_root()) / INFO
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def store(data_dir):
    return Path(data_dir) / "addons"


def cache_dir(data_dir, fingerprint):
    """Kurzer Ordnername: die Dateien darin liegen tief (z. B. _internal/av.libs/avcodec-…dll)."""
    return store(data_dir) / ("speech-" + fingerprint[:12])


def long_path(path):
    """Windows begrenzt normale Pfade auf 260 Zeichen; mit dem Präfix \\\\?\\ gilt die Grenze nicht."""
    path = Path(path).resolve()
    if os.name == "nt" and not str(path).startswith(LONG_PREFIX):
        return LONG_PREFIX + str(path)
    return str(path)


def cached(data_dir, fingerprint):
    try:
        return json.loads((cache_dir(data_dir, fingerprint) / "addon.json").read_text(encoding="utf-8")).get("fingerprint") == fingerprint
    except (OSError, ValueError):
        return False


def speech_status(root=None, data_dir=None):
    """'builtin' (Quellcode-Version), 'installed', 'cached' (im Datenordner, nur noch verlinken), 'missing', 'unsupported'."""
    root = Path(root or application_root())
    if updater.installed_variant(root) is None:
        return "builtin"
    meta = package_info(root).get("speech")
    if not meta:
        return "unsupported"
    try:
        marker = json.loads((root / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        marker = {}
    if marker.get("fingerprint") == meta["fingerprint"] and all(os.path.isfile(long_path(root / rel)) for rel in marker.get("files", [])):
        return "installed"
    if data_dir is not None and cached(data_dir, meta["fingerprint"]):
        return "cached"
    return "missing"


def link_into(cache, root):
    """Dateien der Erweiterung aus dem Datenordner in den App-Ordner verlinken (Hardlink spart Platz)."""
    cache, root = Path(cache), Path(root)
    addon = json.loads((cache / "addon.json").read_text(encoding="utf-8"))
    for rel in addon["files"]:
        source, target = cache / "screenkaffee" / rel, root / rel
        if not target.resolve().is_relative_to(root.resolve()):
            raise AddonError("Die Erweiterung enthält ungültige Pfade.")
        os.makedirs(long_path(target.parent), exist_ok=True)
        if os.path.lexists(long_path(target)):
            os.unlink(long_path(target))
        try:
            os.link(long_path(source), long_path(target))
        except OSError:
            shutil.copy2(long_path(source), long_path(target))
    (root / MARKER).write_text(json.dumps({"fingerprint": addon["fingerprint"], "files": addon["files"]}), encoding="utf-8")
    return addon


def ensure_speech(root=None, data_dir=None):
    """True, wenn die Untertitel-Erkennung jetzt benutzbar ist (verlinkt bei Bedarf aus dem Datenordner)."""
    root = Path(root or application_root())
    status = speech_status(root, data_dir)
    if status in ("builtin", "installed"):
        return True
    if status == "cached":
        meta = package_info(root)["speech"]
        link_into(cache_dir(data_dir, meta["fingerprint"]), root)
        return True
    return False


def install_speech(data_dir, root=None, repo=updater.REPOSITORY, opener=None, progress=None):
    """Erweiterung aus dem Release dieser App-Version laden, prüfen, ablegen und verlinken."""
    from . import __version__
    root = Path(root or application_root())
    meta = package_info(root).get("speech")
    if not meta:
        raise AddonError("Für dieses Paket gibt es keine Untertitel-Erweiterung (z. B. Windows ARM64).")
    release = updater.release_by_tag("v" + __version__, repo, opener)
    folder = store(data_dir)
    archive = updater.download(release, meta["asset"], folder / "download" / meta["asset"], opener, progress)
    temporary = folder / "speech-unpacking"
    shutil.rmtree(temporary, ignore_errors=True)
    try:
        with zipfile.ZipFile(archive) as source:
            for member in source.namelist():
                if not (temporary / member).resolve().is_relative_to(temporary.resolve()):
                    raise AddonError("Die Erweiterung enthält ungültige Pfade.")
            source.extractall(long_path(temporary))
        addon = json.loads((temporary / "addon.json").read_text(encoding="utf-8"))
        if addon.get("fingerprint") != meta["fingerprint"]:
            raise AddonError("Die heruntergeladene Erweiterung passt nicht zu dieser screenkaffee-Version.")
        for old in folder.glob("speech-*"):
            if old.is_dir() and old != temporary:
                shutil.rmtree(long_path(old), ignore_errors=True)
        cache = cache_dir(data_dir, meta["fingerprint"])
        temporary.rename(cache)
        missing = [rel for rel in addon["files"] if not os.path.isfile(long_path(cache / "screenkaffee" / rel))]
        if missing:
            shutil.rmtree(long_path(cache), ignore_errors=True)
            raise AddonError(f"Die Erweiterung wurde unvollständig entpackt ({len(missing)} Dateien fehlen).")
    finally:
        shutil.rmtree(long_path(temporary), ignore_errors=True)
        archive.unlink(missing_ok=True)
        shutil.rmtree(folder / "download", ignore_errors=True)
    return link_into(cache, root)


def remove_speech(data_dir, root=None):
    """Verlinkte Dateien und die abgelegte Erweiterung löschen; liefert die frei gewordenen Bytes (ungefähr)."""
    root = Path(root or application_root())
    freed = 0
    try:
        marker = json.loads((root / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        marker = {}
    for rel in marker.get("files", []):
        target = root / rel
        if target.resolve().is_relative_to(root.resolve()) and os.path.isfile(long_path(target)):
            os.unlink(long_path(target))
            parent = target.parent
            while parent != root and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
    (root / MARKER).unlink(missing_ok=True)
    for cache in store(data_dir).glob("speech-*"):
        for folder, _, names in os.walk(long_path(cache)):
            freed += sum(os.path.getsize(os.path.join(folder, name)) for name in names)
        shutil.rmtree(long_path(cache), ignore_errors=True)
    return freed
