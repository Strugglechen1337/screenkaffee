"""User-triggered live scroll frames, retained locally for recovery."""
import json
from pathlib import Path
import uuid
from .color import save_png, import_srgb
from .stitch import stitch_scroll
from .i18n import tr


class ManualScrollSession:
    def __init__(self, folder, bounds, **options):
        self.folder = Path(folder) / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.bounds, self.options, self.paths = list(bounds), options, []

    def append(self, image):
        if len(self.paths) >= 200:
            raise ValueError(tr("Maximal 200 Scrollbilder pro Serie."))
        if self.paths and import_srgb(self.paths[0]).size != image.size:
            raise ValueError(tr("Der Scrollbereich hat seine Größe geändert."))
        path = self.folder / f"{len(self.paths):04}.png"
        save_png(image, path)
        self.paths.append(path)
        content = {"bounds": self.bounds, "options": self.options, "frames": [p.name for p in self.paths]}
        temporary = self.folder / "recovery.tmp"
        temporary.write_text(json.dumps(content), encoding="utf-8")
        temporary.replace(self.folder / "recovery.json")
        return len(self.paths)

    def finish(self):
        return stitch_scroll([import_srgb(path) for path in self.paths], **self.options)
