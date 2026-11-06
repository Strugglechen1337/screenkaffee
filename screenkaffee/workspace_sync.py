"""Explicit local-folder exchange. No network, deletions or SQLite-file sharing."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_json(path, data):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        shutil.copy2(source, temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def record(workspace, ident):
    row = dict(workspace.get(ident))
    path = workspace.path(ident)
    names = [path.name, ident + ".thumb.png", path.with_suffix(".prism.json").name,
             path.with_suffix(".screenkaffee.json").name, path.with_suffix(".srt").name, path.with_suffix(".txt").name]
    files = {name: file_hash(workspace.images / name) for name in dict.fromkeys(names) if (workspace.images / name).is_file()}
    value = {"row": row, "files": files}
    value["digest"] = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return value


def validate_record(ident, value, folder):
    if not re.fullmatch(r"[0-9a-f]{32}", ident) or value["row"]["id"] != ident:
        raise ValueError("Ungültige Aufnahme-ID im Austauschordner.")
    for name, expected in value["files"].items():
        if Path(name).name != name or any(c in name for c in "/\\:") or not name.startswith(ident + ".") or (folder / name).is_symlink():
            raise ValueError("Ungültiger Dateipfad im Austauschordner.")
        if file_hash(folder / name) != expected:
            raise ValueError("Datei im Austauschordner wurde während der Synchronisierung verändert.")
    if value["row"]["filename"] not in value["files"]:
        raise ValueError("Originaldatei fehlt im Austauschordner.")
    content = {"row": value["row"], "files": value["files"]}
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if digest != value["digest"] or value["row"].get("kind") not in ("image", "video", "document"):
        raise ValueError("Ungültige Medienbeschreibung im Austauschordner.")


def import_record(workspace, value, media, conflict=False):
    original = value["row"]["id"]
    row = dict(value["row"])
    ident = uuid.uuid4().hex if conflict else original
    existing = workspace.get(ident)
    if existing:
        backup = workspace.root / "sync-backups" / uuid.uuid4().hex
        backup.mkdir(parents=True)
        previous = record(workspace, ident)
        for name in previous["files"]:
            shutil.copy2(workspace.images / name, backup / name)
        atomic_json(backup / "record.json", previous)
    for name in value["files"]:
        target_name = ident + name[len(original):]
        copy_file(media / name, workspace.images / target_name)
    row["id"] = ident
    row["filename"] = ident + row["filename"][len(original):]
    if conflict:
        row["title"] += " (Konfliktkopie)"
    columns = ("id", "title", "created", "filename", "width", "height", "tags", "collection", "metadata", "kind")
    workspace.db.execute("INSERT OR REPLACE INTO captures (" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")",
                         [row[key] for key in columns])
    workspace.db.commit()
    return ident


def sync_workspace(workspace, folder, propagate_deletions=False):
    folder = Path(folder).resolve()
    if folder == workspace.root.resolve() or folder.is_relative_to(workspace.images.resolve()):
        raise ValueError("Bitte einen separaten Austauschordner auswählen.")
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / ".screenkaffee.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("Der Austauschordner ist gesperrt. Bei abgebrochenem Austausch die Sperrdatei erst nach Prüfung anderer Instanzen entfernen.") from exc
    os.close(descriptor)
    try:
        manifest = folder / "screenkaffee-index.json"
        remote = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {"version": 1, "captures": {}}
        if remote.get("version") != 1 or not isinstance(remote.get("captures"), dict):
            raise ValueError("Unbekanntes Austauschformat.")
        media = folder / "media"
        if media.is_symlink():
            raise ValueError("Der Medienordner darf keine symbolische Verknüpfung sein.")
        media.mkdir(exist_ok=True)
        records = remote["captures"]
        tombstones = remote.setdefault("deleted", {}) if propagate_deletions else {}
        # Validate every remote file before changing any local capture.
        for ident, value in records.items():
            validate_record(ident, value, media)
        state_file = workspace.root / ("sync-state-" + hashlib.sha256(str(folder).encode()).hexdigest()[:16] + ".json")
        state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
        local = {row["id"]: record(workspace, row["id"]) for row in workspace.rows()}
        imported, exported, conflicts, deleted = 0, 0, 0, 0
        if propagate_deletions:
            for ident in sorted(set(state) - set(local)):
                if ident in records and records[ident]["digest"] == state[ident]:
                    for name in records[ident]["files"]:
                        (media / name).unlink(missing_ok=True)
                    tombstones[ident] = records.pop(ident)["digest"]
                    deleted += 1
            for ident, old_digest in list(tombstones.items()):
                if ident in local and local[ident]["digest"] == old_digest:
                    workspace.remove(ident)
                    local.pop(ident, None)
                    deleted += 1
        for ident in sorted(set(local) | set(records)):
            own, other = local.get(ident), records.get(ident)
            if other and (not own or own["digest"] != other["digest"]):
                if not own or (state.get(ident) == own["digest"]):
                    import_record(workspace, other, media)
                    local[ident] = record(workspace, ident)
                    imported += 1
                elif state.get(ident) != other["digest"]:
                    duplicate = import_record(workspace, other, media, conflict=True)
                    local[duplicate] = record(workspace, duplicate)
                    conflicts += 1
        for ident, value in local.items():
            if records.get(ident, {}).get("digest") != value["digest"]:
                for name in value["files"]:
                    copy_file(workspace.images / name, media / name)
                records[ident] = value
                if propagate_deletions:
                    tombstones.pop(ident, None)
                exported += 1
        atomic_json(manifest, remote)
        atomic_json(state_file, {ident: value["digest"] for ident, value in local.items()})
        result = {"imported": imported, "exported": exported, "conflicts": conflicts}
        if propagate_deletions:
            result["deleted"] = deleted
        return result
    finally:
        lock.unlink(missing_ok=True)
