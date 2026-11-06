from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid
import shutil
from .color import save_png, import_srgb


def parse_tags(value):
    """Zerlegt einen Tag-String oder eine Liste in eindeutige Tags in Originalreihenfolge."""
    if isinstance(value, (list, tuple, set)):
        raw = [str(x).strip() for x in value]
    else:
        text = str(value or "")
        raw = [x.strip() for x in (text.split(",") if "," in text else text.split())]
    seen, result = set(), []
    for tag in raw:
        if tag and tag.casefold() not in seen:
            seen.add(tag.casefold())
            result.append(tag)
    return result


def portable_root():
    override = os.environ.get("SCREENKAFFEE_DATA")
    if override:
        return Path(override).resolve()
    if os.environ.get("SCREENKAFFEE_APPDIR"):
        return Path(os.environ["SCREENKAFFEE_APPDIR"]).resolve() / "data"
    executable = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    return executable / "data"


class ThumbnailCache:
    """Kleine persistente Vorschauen mit begrenzter Dateianzahl."""
    PRUNE_EVERY = 64

    def __init__(self, root, limit=5000):
        self.root = Path(root) / "thumbnails"
        self.limit = max(1, limit)
        self.created = 0

    def target(self, source):
        import hashlib
        source = Path(source)
        stat = source.stat()
        signature = f"{source.resolve()}:{stat.st_mtime_ns}:{stat.st_size}"
        return self.root / (hashlib.sha256(signature.encode()).hexdigest() + ".png")

    def cached(self, source):
        """Vorhandene Vorschau ohne Dekodieren, sonst None."""
        target = self.target(source)
        if target.exists():
            target.touch()
            return target
        return None

    def get(self, source):
        from PIL import Image
        target = self.cached(source)
        if target is not None:
            return target
        target = self.target(source)
        self.root.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as image:
            image.thumbnail((344, 236), Image.Resampling.LANCZOS)
            image.convert("RGBA").save(target)
        # Aufräumen nur gelegentlich, damit das erste Befüllen nicht quadratisch teuer wird.
        self.created += 1
        if self.limit >= self.PRUNE_EVERY and self.created % self.PRUNE_EVERY:
            return target
        files = sorted(self.root.glob("*.png"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
        for path in files[self.limit:]:
            path.unlink(missing_ok=True)
        return target


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
        if "favorite" not in columns:
            self.db.execute("ALTER TABLE captures ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")
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
        copied_sidecars = []
        thumb_path = self.images / (ident + ".thumb.png")
        try:
            for suffix in (".srt", ".txt"):
                sidecar = source.with_suffix(suffix)
                if sidecar.is_file() and sidecar != source:
                    dst_sidecar = target.with_suffix(suffix)
                    copied_sidecars.append(dst_sidecar)
                    shutil.copy2(sidecar, dst_sidecar)
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
            for sc in copied_sidecars:
                sc.unlink(missing_ok=True)
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

    def _matches_query(self, row, query):
        needle = query.casefold()
        if needle in (row["title"] or "").casefold() or needle in (row["tags"] or "").casefold():
            return True
        try:
            meta = json.loads(row["metadata"] or "{}")
        except (ValueError, TypeError):
            meta = {}
        if isinstance(meta, dict):
            for key in ("window", "window_title", "app", "application", "process_name", "url"):
                val = meta.get(key)
                if val and needle in str(val).casefold():
                    return True
        path = self.images / row["filename"]
        for suffix in (".srt", ".txt"):
            sidecar = path.with_suffix(suffix)
            if sidecar != path and sidecar.is_file():
                try:
                    if needle in sidecar.read_text(encoding="utf-8", errors="replace").casefold():
                        return True
                except OSError:
                    pass
        return False

    def rows(self, query="", collection=None, kind=None, sort="newest", days=None, favorites_only=False):
        sql = "SELECT * FROM captures WHERE 1=1"
        args = []
        if collection and collection != "Alle Aufnahmen":
            sql += " AND collection=?"
            args.append(collection)
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if favorites_only:
            sql += " AND favorite=1"
        if days:
            sql += " AND julianday(created)>=julianday(?)"
            args.append((datetime.now(timezone.utc) - timedelta(days=float(days))).isoformat())
        orders = {"newest": "created DESC, rowid DESC", "oldest": "created ASC, rowid ASC",
                  "title": "title COLLATE NOCASE, rowid DESC", "type": "kind, created DESC", "size": "width*height DESC, rowid DESC"}
        if sort not in orders:
            raise ValueError("Unbekannte Sortierung.")
        fetched = self.db.execute(sql + " ORDER BY " + orders[sort], args).fetchall()
        if not query:
            return fetched
        return [row for row in fetched if self._matches_query(row, query)]

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

    def set_favorite(self, ident, favorite=True):
        ids = [ident] if isinstance(ident, str) else list(ident)
        for item in ids:
            if self.get(item) is None:
                raise KeyError(item)
        flag = 1 if favorite else 0
        self.db.executemany("UPDATE captures SET favorite=? WHERE id=?", [(flag, item) for item in ids])
        self.db.commit()

    def toggle_favorite(self, ident):
        row = self.get(ident)
        if row is None:
            raise KeyError(ident)
        new_flag = 0 if row["favorite"] else 1
        self.db.execute("UPDATE captures SET favorite=? WHERE id=?", (new_flag, ident))
        self.db.commit()
        return bool(new_flag)

    def update_tags(self, ids, add=(), remove=(), collection=None):
        ids = list(ids)
        if any(self.get(ident) is None for ident in ids):
            raise KeyError("Aufnahme nicht gefunden.")
        add_list = parse_tags(add)
        remove_set = {t.casefold() for t in parse_tags(remove)}
        target_col = collection.strip() if isinstance(collection, str) and collection.strip() else None
        if target_col and target_col != self.DEFAULT_COLLECTION:
            self.create_collection(target_col)
        for ident in ids:
            row = self.get(ident)
            if row is None:
                raise KeyError(ident)
            current = [t for t in parse_tags(row["tags"]) if t.casefold() not in remove_set]
            existing = {t.casefold() for t in current}
            for tag in add_list:
                if tag.casefold() not in existing:
                    current.append(tag)
                    existing.add(tag.casefold())
            col = target_col if target_col is not None else row["collection"]
            self.db.execute("UPDATE captures SET tags=?, collection=? WHERE id=?", (", ".join(current), col, ident))
        self.db.commit()

    def add_tags(self, ids, tags):
        self.update_tags(ids, add=tags)

    def remove_tags(self, ids, tags):
        self.update_tags(ids, remove=tags)

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
            if row["id"] in exclude or row["favorite"]:
                continue
            older = datetime.fromisoformat(row["created"])
            if older.tzinfo is None:
                older = older.replace(tzinfo=timezone.utc)
            if (keep_latest and index >= keep_latest) or (max_age_days and older < threshold):
                result.append(row["id"])
        return result

    def remove(self, ident):
        # Erst alle Dateien verschieben, dann den Datenbankeintrag entfernen.
        path = self.path(ident)
        trash = self.root / "trash"
        trash.mkdir(exist_ok=True)
        info = trash / (ident + ".metadata.json")
        if any(p != info for p in trash.glob(ident + ".*")):
            raise ValueError("Bitte zuerst die unvollständige Löschung wiederherstellen.")
        info.write_text(json.dumps(dict(self.get(ident)), ensure_ascii=False, indent=2), encoding="utf-8")
        files = [path, self.images / (ident + ".thumb.png")]
        files += [path.with_suffix(suffix) for suffix in (".prism.json", ".screenkaffee.json", ".srt", ".txt")]
        moved = []
        try:
            for source in dict.fromkeys(files):
                if source.exists():
                    target = trash / source.name
                    if target.exists():
                        raise ValueError("Datei liegt bereits im Papierkorb: " + source.name)
                    source.replace(target)
                    moved.append((source, target))
            self.db.execute("DELETE FROM captures WHERE id=?", (ident,))
            self.db.commit()
        except Exception:
            self.db.rollback()
            for source, target in reversed(moved):
                try:
                    target.replace(source)
                except OSError:
                    pass  # Metadaten bleiben für die Wiederherstellung erhalten.
            if not any(target.exists() for _, target in moved):
                info.unlink(missing_ok=True)
            raise

    def trash_entries(self):
        result = []
        for path in (self.root / "trash").glob("*.metadata.json"):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                if not self.get(row["id"]) or any(p.name != path.name for p in path.parent.glob(row["id"] + ".*")):
                    result.append(row)
            except (ValueError, KeyError):
                pass
        return result

    def restore(self, ident):
        import re
        if not re.fullmatch(r"[a-f0-9]{32}", ident):
            raise ValueError("Ungültige Aufnahme-ID.")
        existing = self.get(ident)
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
        if filename not in files and not (existing and path.exists()):
            raise ValueError("Originaldatei fehlt im Papierkorb.")
        for name in files:
            if (self.images / name).exists():
                raise ValueError("Zieldatei existiert bereits: " + name)
        columns = ("id", "title", "created", "filename", "width", "height", "tags", "collection", "metadata", "kind", "favorite")
        moved = []
        try:
            for name in files:
                (trash / name).replace(self.images / name)
                moved.append(name)
            self.db.execute("INSERT OR REPLACE INTO captures (" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")",
                [row.get(key, "image" if key == "kind" else (0 if key == "favorite" else "")) for key in columns])
            self.db.commit()
        except Exception:
            self.db.rollback()
            for name in reversed(moved):
                (self.images / name).replace(trash / name)
            raise
        info.unlink()
        return ident

    def delete_trashed(self, ids):
        """Nur vollständig gelöschte Aufnahmen endgültig entfernen."""
        import re
        freed = 0
        for ident in ids:
            if self.get(ident):
                continue
            if not re.fullmatch(r"[a-f0-9]{32}", ident):
                raise ValueError("Ungültige Aufnahme-ID.")
            for path in (self.root / "trash").glob(ident + ".*"):
                if path.is_file():
                    size = path.stat().st_size
                    path.unlink()
                    freed += size
        return freed

    def empty_trash(self):
        """Aktive Datenbankeinträge bleiben auch bei unvollständigem Rollback geschützt."""
        return self.delete_trashed([row["id"] for row in self.trash_entries()])

    def close(self):
        self.db.close()


def details_dialog(parent, workspace, ids, on_export_copy=None):
    """Ein Dialog für Titel, Tags und Sammlung (Einzelauswahl) bzw. Tags hinzufügen/entfernen und Sammlung (Mehrfachauswahl)."""
    from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit
    ids = list(ids)
    dialog = QDialog(parent)
    form = QFormLayout(dialog)
    dialog._applied = False
    collections = [workspace.DEFAULT_COLLECTION] + [c for c in workspace.collections() if c != workspace.DEFAULT_COLLECTION]
    if len(ids) == 1:
        ident = ids[0]
        row = workspace.get(ident)
        dialog.setWindowTitle("Details bearbeiten")
        dialog.title_edit = QLineEdit(row["title"])
        dialog.tags_edit = QLineEdit(row["tags"])
        dialog.collection_combo = QComboBox()
        dialog.collection_combo.setEditable(True)
        dialog.collection_combo.addItems(collections)
        dialog.collection_combo.setCurrentText(row["collection"])
        dialog.collection_edit = dialog.collection_combo.lineEdit()
        form.addRow("Titel", dialog.title_edit)
        form.addRow("Suchbegriffe / Tags", dialog.tags_edit)
        form.addRow("Sammlung", dialog.collection_combo)

        def apply():
            if dialog._applied:
                return
            dialog._applied = True
            from .batch import split_title_extension
            title, extension = split_title_extension(dialog.title_edit.text().strip())
            if extension and row["kind"] == "image" and on_export_copy is not None:
                on_export_copy(ident, title, extension)
            col = dialog.collection_combo.currentText().strip() or workspace.DEFAULT_COLLECTION
            if col != workspace.DEFAULT_COLLECTION:
                workspace.create_collection(col)
            workspace.update(ident, title or row["title"], dialog.tags_edit.text().strip(), col)
    else:
        dialog.setWindowTitle(f"Details bearbeiten ({len(ids)} Aufnahmen)")
        dialog.add_tags_edit = QLineEdit()
        dialog.add_tags_edit.setPlaceholderText("Kommagetrennte Tags …")
        dialog.remove_tags_edit = QLineEdit()
        dialog.remove_tags_edit.setPlaceholderText("Zu entfernende Tags …")
        dialog.collection_combo = QComboBox()
        dialog.collection_combo.setEditable(True)
        dialog.collection_combo.addItem("", None)
        dialog.collection_combo.addItems(collections)
        dialog.collection_combo.setCurrentText("")
        dialog.collection_edit = dialog.collection_combo.lineEdit()
        form.addRow("Tags hinzufügen", dialog.add_tags_edit)
        form.addRow("Tags entfernen", dialog.remove_tags_edit)
        form.addRow("Sammlung (leer = unverändert)", dialog.collection_combo)

        def apply():
            if dialog._applied:
                return
            dialog._applied = True
            col = dialog.collection_combo.currentText().strip() or None
            workspace.update_tags(ids, add=dialog.add_tags_edit.text(), remove=dialog.remove_tags_edit.text(), collection=col)

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    dialog.accepted.connect(apply)
    dialog.apply = apply
    form.addRow(buttons)
    return dialog
