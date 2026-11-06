"""Untertitel-Erweiterung (Whisper-Modell + ctranslate2/PyAV/tokenizers) aus den Programmpaketen heraustrennen.

Die Paket-Skripte sortieren damit ihre Dateien: Was zur Erweiterung gehört, landet in einem eigenen Archiv
``screenkaffee-Untertitel-<Plattform>.zip`` (Release-Asset), das Hauptpaket bekommt eine ``addons.json`` mit
Asset-Name und Fingerabdruck. Die App lädt die Erweiterung erst auf Wunsch (``screenkaffee/addons.py``).
"""
import hashlib
import json
from pathlib import Path
import zipfile

# Ordner direkt unter site-packages bzw. _internal, die nur die Untertitel-Erkennung braucht.
SPEECH_TOPS = {"av", "av.libs", "ctranslate2", "ctranslate2.libs", "tokenizers", "tokenizers.libs", "faster_whisper"}
SPEECH_MODEL = "models/whisper-base"
SPEECH_ASSETS = {"windows-x64": "screenkaffee-Untertitel-Windows-x64.zip",
                 "linux-x86_64": "screenkaffee-Untertitel-Linux-x86_64.zip"}


def is_speech_site_path(rel):
    """rel relativ zu site-packages/_internal, z. B. 'av.libs/avcodec-61.dll'."""
    return rel.split("/", 1)[0] in SPEECH_TOPS


def is_speech_model_path(rel):
    """rel relativ zum App-Ordner, z. B. 'models/whisper-base/model.bin'."""
    return rel == SPEECH_MODEL or rel.startswith(SPEECH_MODEL + "/")


def fingerprint(entries):
    """entries: [(Pfad im App-Ordner, Quelldatei)]. SHA256 über sortierte Pfade und Dateiinhalte."""
    digest = hashlib.sha256()
    for rel, source in sorted(entries):
        file_hash = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        digest.update(f"{rel}\t{file_hash}\n".encode("utf-8"))
    return digest.hexdigest()


def write_addon(entries, archive):
    """Erweiterungsarchiv schreiben; liefert den Eintrag für addons.json."""
    archive = Path(archive)
    entries = sorted(entries)
    if not any(rel.endswith("/model.bin") for rel, _ in entries):
        raise RuntimeError("Das Whisper-Modell fehlt (scripts/download_speech_model.py ausführen).")
    value = fingerprint(entries)
    total = sum(Path(source).stat().st_size for _, source in entries)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        output.writestr("addon.json", json.dumps({"name": "speech", "fingerprint": value,
                                                  "files": [rel for rel, _ in entries]}, indent=1))
        for rel, source in entries:
            output.write(source, "screenkaffee/" + rel)
    return {"speech": {"asset": archive.name, "fingerprint": value, "bytes": total, "files": len(entries)}}


def model_entries(root):
    """Modelldateien aus dem Projekt als (Pfad im App-Ordner, Quelle)."""
    folder = Path(root) / SPEECH_MODEL
    return [(path.relative_to(root).as_posix(), path) for path in folder.rglob("*") if path.is_file()]
