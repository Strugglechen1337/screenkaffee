from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid
import shutil
from .color import save_png, import_srgb


def portable_root():
    override = os.environ.get("SCREENKAFFEE_DATA")
    if override:
        return Path(override).resolve()
    if os.environ.get("SCREENKAFFEE_APPDIR"):
        return Path(os.environ["SCREENKAFFEE_APPDIR"]).resolve() / "data"
    executable = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    return executable / "data"


class Workspace:
    def __init__(self, root=None):
        self.root = Path(root) if root else portable_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.images = self.root / "captures"
        self.images.mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / "workspace.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS captures (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, created TEXT NOT NULL,
            filename TEXT NOT NULL, width INTEGER, height INTEGER,
            tags TEXT DEFAULT '', collection TEXT DEFAULT 'Alle Aufnahmen',
            metadata TEXT NOT NULL)""")
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(captures)")}
        if "kind" not in columns:
            self.db.execute("ALTER TABLE captures ADD COLUMN kind TEXT NOT NULL DEFAULT 'image'")
        self.db.execute("CREATE TABLE IF NOT EXISTS collections(name TEXT PRIMARY KEY)")
        collection_columns = {row[1] for row in self.db.execute("PRAGMA table_info(collections)")}
        if "position" not in collection_columns:
            self.db.execute("ALTER TABLE collections ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
        if "icon" not in collection_columns:
            self.db.execute("ALTER TABLE collections ADD COLUMN icon TEXT NOT NULL DEFAULT ''")
        self.db.execute("INSERT OR IGNORE INTO collections(name) SELECT DISTINCT collection FROM captures")
        self.db.commit()

    def add(self, image, title=None, metadata=None, collection="Alle Aufnahmen"):
        ident = uuid.uuid4().hex
        created = datetime.now().astimezone().isoformat(timespec="seconds")
        title = title or datetime.now().strftime("Aufnahme %d.%m.%Y %H:%M:%S")
        filename = ident + ".png"
        metadata = metadata or {}
        save_png(image, self.images / filename, metadata)
        try:
            self.db.execute("INSERT INTO captures (id,title,created,filename,width,height,tags,collection,metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ident, title, created, filename, image.width, image.height, "", collection,
                 json.dumps(metadata, ensure_ascii=False)))
            self.db.commit()
        except Exception:
            (self.images / filename).unlink(missing_ok=True)
            raise
        return ident

    def add_file(self, path, kind="video", title=None, metadata=None, thumbnail=None, collection="Alle Aufnahmen"):
        source = Path(path).resolve()
        if kind not in ("video", "document") or not source.is_file():
            raise ValueError("Ungültige Mediendatei.")
        ident = uuid.uuid4().hex
        filename = ident + source.suffix.lower()
        target = self.images / filename
        shutil.copy2(source, target)
        thumb_path = self.images / (ident + ".thumb.png")
        try:
            if thumbnail:
                save_png(thumbnail, thumb_path)
            self.db.execute("INSERT INTO captures (id,title,created,filename,width,height,tags,collection,metadata,kind) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ident, title or source.stem, datetime.now().astimezone().isoformat(timespec="seconds"), filename,
                 thumbnail.width if thumbnail else 0, thumbnail.height if thumbnail else 0, "", collection,
                 json.dumps(metadata or {}, ensure_ascii=False), kind))
            self.db.commit()
        except Exception:
            target.unlink(missing_ok=True)
            thumb_path.unlink(missing_ok=True)
            raise
        return ident

    def thumbnail_path(self, ident):
        row = self.get(ident)
        thumb = self.images / (ident + ".thumb.png")
        return thumb if (row["kind"] != "image" or thumb.exists()) else self.path(ident)

    def update_rendered_thumbnail(self, ident, image):
        if self.get(ident) is None:
            return
        save_png(image, self.images / (ident + ".thumb.png"))
        self.db.execute("UPDATE captures SET width=?, height=? WHERE id=?", (image.width, image.height, ident))
        self.db.commit()

    def rows(self, query="", collection=None, kind=None, sort="newest", days=None):
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        sql = "SELECT * FROM captures WHERE (title LIKE ? ESCAPE '\\' OR tags LIKE ? ESCAPE '\\')"
        args = [pattern, pattern]
        if collection and collection != "Alle Aufnahmen":
            sql += " AND collection=?"
            args.append(collection)
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if days:
            sql += " AND julianday(created)>=julianday(?)"
            args.append((datetime.now(timezone.utc) - timedelta(days=float(days))).isoformat())
        orders = {"newest": "created DESC, rowid DESC", "oldest": "created ASC, rowid ASC",
                  "title": "title COLLATE NOCASE, rowid DESC", "type": "kind, created DESC", "size": "width*height DESC, rowid DESC"}
        if sort not in orders:
            raise ValueError("Unbekannte Sortierung.")
        return self.db.execute(sql + " ORDER BY " + orders[sort], args).fetchall()

    def get(self, ident):
        return self.db.execute("SELECT * FROM captures WHERE id=?", (ident,)).fetchone()

    def path(self, ident):
        row = self.get(ident)
        if row is None:
            raise KeyError(ident)
        return self.images / row["filename"]

    def image(self, ident):
        return import_srgb(self.path(ident))

    def project_path(self, ident):
        path = self.path(ident)
        current, legacy = path.with_suffix(".screenkaffee.json"), path.with_suffix(".prism.json")
        return current if current.exists() or not legacy.exists() else legacy

    def update(self, ident, title, tags, collection):
        self.db.execute("UPDATE captures SET title=?, tags=?, collection=? WHERE id=?",
                        (title, tags, collection, ident))
        self.db.commit()

    DEFAULT_COLLECTION = "Alle Aufnahmen"

    def collections(self):
        """Collection names in the user's order (position, then name), including any only referenced by captures."""
        names = [x[0] for x in self.db.execute("SELECT name FROM collections ORDER BY position, name COLLATE NOCASE")]
        extra = [x[0] for x in self.db.execute("SELECT DISTINCT collection FROM captures ORDER BY 1") if x[0] not in names]
        return names + extra

    def create_collection(self, name):
        name = name.strip()
        if not name or len(name) > 200:
            raise ValueError("Sammlungsname muss 1 bis 200 Zeichen enthalten.")
        self.db.execute("INSERT OR IGNORE INTO collections(name) VALUES (?)", (name,))
        self.db.commit()

    def _editable_collection(self, name):
        if name == self.DEFAULT_COLLECTION:
            raise ValueError("Die Sammlung „Alle Aufnahmen“ lässt sich nicht ändern.")
        if name not in self.collections():
            raise ValueError("Sammlung nicht gefunden.")

    def rename_collection(self, old, new):
        new = new.strip()
        self._editable_collection(old)
        if not new or len(new) > 200:
            raise ValueError("Sammlungsname muss 1 bis 200 Zeichen enthalten.")
        if new == self.DEFAULT_COLLECTION or (new != old and new in self.collections()):
            raise ValueError("Eine Sammlung mit diesem Namen existiert bereits.")
        self.create_collection(old)
        self.db.execute("UPDATE collections SET name=? WHERE name=?", (new, old))
        self.db.execute("UPDATE captures SET collection=? WHERE collection=?", (new, old))
        self.db.commit()

    def delete_collection(self, name, delete_files=False):
        """Remove a collection. Its captures go back to "Alle Aufnahmen", or into the trash with delete_files=True."""
        self._editable_collection(name)
        ids = [row[0] for row in self.db.execute("SELECT id FROM captures WHERE collection=?", (name,))]
        if delete_files:
            for ident in ids:
                self.remove(ident)
        else:
            self.db.execute("UPDATE captures SET collection=? WHERE collection=?", (self.DEFAULT_COLLECTION, name))
        self.db.execute("DELETE FROM collections WHERE name=?", (name,))
        self.db.commit()
        return len(ids)

    def move_to_collection(self, ids, name):
        name = name.strip() or self.DEFAULT_COLLECTION
        if name != self.DEFAULT_COLLECTION:
            self.create_collection(name)
        for ident in ids:
            if self.get(ident) is None:
                raise KeyError(ident)
        self.db.executemany("UPDATE captures SET collection=? WHERE id=?", [(name, ident) for ident in ids])
        self.db.commit()

    def move_collection(self, name, delta):
        """Move a collection up (-1) or down (+1) in the list; returns False at the ends."""
        self._editable_collection(name)
        names = [n for n in self.collections() if n != self.DEFAULT_COLLECTION]
        index = names.index(name)
        target = index + delta
        if not 0 <= target < len(names):
            return False
        names[index], names[target] = names[target], names[index]
        for position, item in enumerate(names):
            self.db.execute("INSERT OR IGNORE INTO collections(name) VALUES (?)", (item,))
            self.db.execute("UPDATE collections SET position=? WHERE name=?", (position, item))
        self.db.commit()
        return True

    def collection_icon(self, name):
        row = self.db.execute("SELECT icon FROM collections WHERE name=?", (name,)).fetchone()
        return row[0] if row else ""

    def set_collection_icon(self, name, icon):
        self._editable_collection(name)
        self.create_collection(name)
        self.db.execute("UPDATE collections SET icon=? WHERE name=?", (icon, name))
        self.db.commit()

    def cleanup_candidates(self, keep_latest=0, max_age_days=0, exclude=()):
        if keep_latest < 0 or max_age_days < 0:
            raise ValueError("Aufbewahrungswerte dürfen nicht negativ sein.")
        threshold = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        result = []
        for index, row in enumerate(self.rows()):
            if row["id"] in exclude:
                continue
            older = datetime.fromisoformat(row["created"])
            if older.tzinfo is None:
                older = older.replace(tzinfo=timezone.utc)
            if (keep_latest and index >= keep_latest) or (max_age_days and older < threshold):
                result.append(row["id"])
        return result

    def remove(self, ident):
        # UI requires confirmation; originals are moved to a local trash folder.
        path = self.path(ident)
        trash = self.root / "trash"
        trash.mkdir(exist_ok=True)
        (trash / (ident + ".metadata.json")).write_text(json.dumps(dict(self.get(ident)), ensure_ascii=False, indent=2), encoding="utf-8")
        if path.exists():
            path.replace(trash / path.name)
        for suffix in (".prism.json", ".screenkaffee.json"):
            project = path.with_suffix(suffix)
            if project.exists():
                project.replace(trash / project.name)
        thumb = self.images / (ident + ".thumb.png")
        if thumb.exists():
            thumb.replace(trash / thumb.name)
        for suffix in (".srt", ".txt"):
            sidecar = path.with_suffix(suffix)
            if sidecar.exists() and sidecar != path:
                sidecar.replace(trash / sidecar.name)
        self.db.execute("DELETE FROM captures WHERE id=?", (ident,))
        self.db.commit()

    def trash_entries(self):
        result = []
        for path in (self.root / "trash").glob("*.metadata.json"):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                if not self.get(row["id"]):
                    result.append(row)
            except (ValueError, KeyError):
                pass
        return result

    def restore(self, ident):
        import re
        if not re.fullmatch(r"[a-f0-9]{32}", ident):
            raise ValueError("Ungültige Aufnahme-ID.")
        if self.get(ident):
            raise ValueError("Aufnahme ist bereits vorhanden.")
        trash = self.root / "trash"
        info = trash / (ident + ".metadata.json")
        row = json.loads(info.read_text(encoding="utf-8"))
        filename = row["filename"]
        if row.get("id") != ident or Path(filename).name != filename or not filename.startswith(ident + "."):
            raise ValueError("Ungültiger Aufnahmedateiname.")
        path = self.images / filename
        files = [filename, ident + ".thumb.png", path.with_suffix(".prism.json").name, path.with_suffix(".screenkaffee.json").name,
                 path.with_suffix(".srt").name, path.with_suffix(".txt").name]
        files = [name for name in dict.fromkeys(files) if (trash / name).exists()]
        if filename not in files:
            raise ValueError("Originaldatei fehlt im Papierkorb.")
        for name in files:
            if (self.images / name).exists():
                raise ValueError("Zieldatei existiert bereits: " + name)
        columns = ("id", "title", "created", "filename", "width", "height", "tags", "collection", "metadata", "kind")
        moved = []
        try:
            for name in files:
                (trash / name).replace(self.images / name)
                moved.append(name)
            self.db.execute("INSERT INTO captures (" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")",
                [row.get(key, "image" if key == "kind" else "") for key in columns])
            self.db.commit()
        except Exception:
            self.db.rollback()
            for name in reversed(moved):
                (self.images / name).replace(trash / name)
            raise
        info.unlink()
        return ident

    def empty_trash(self):
        """Papierkorb endgültig leeren (nur nach Rückfrage in der Oberfläche); liefert die freigegebenen Bytes."""
        trash = self.root / "trash"
        freed = 0
        if trash.is_dir():
            for path in trash.iterdir():
                if path.is_file():
                    freed += path.stat().st_size
                    path.unlink()
        return freed

    def close(self):
        self.db.close()

