from email import policy
from email.parser import BytesParser
import json
import pytest
from PIL import Image
from screenkaffee.workspace import Workspace
from screenkaffee.workspace_sync import sync_workspace
from screenkaffee.local_share import save_mail_draft
from screenkaffee.color_picker import color_formats


def test_local_exchange_pull_updates_and_conflict_copies(tmp_path):
    a, b = Workspace(tmp_path / "a"), Workspace(tmp_path / "b")
    shared = tmp_path / "shared"
    ident = a.add(Image.new("RGB", (30, 20), "red"), "Start")
    assert sync_workspace(a, shared)["exported"] == 1
    assert sync_workspace(b, shared)["imported"] == 1
    assert b.image(ident).tobytes() == a.image(ident).tobytes()
    a.update(ident, "Version 2", "", "Sammlung")
    sync_workspace(a, shared)
    sync_workspace(b, shared)
    assert b.get(ident)["title"] == "Version 2"
    assert any((b.root / "sync-backups").iterdir())
    a.update(ident, "Änderung A", "", "Sammlung")
    b.update(ident, "Änderung B", "", "Sammlung")
    sync_workspace(a, shared)
    result = sync_workspace(b, shared)
    assert result["conflicts"] == 1
    assert {r["title"] for r in b.rows()} == {"Änderung B", "Änderung A (Konfliktkopie)"}
    sync_workspace(a, shared)
    assert {r["title"] for r in a.rows()} == {r["title"] for r in b.rows()}
    assert sync_workspace(b, shared) == {"imported": 0, "exported": 0, "conflicts": 0}
    a.close()
    b.close()


def test_corrupt_shared_file_and_locks_preserve_local_originals(tmp_path):
    a = Workspace(tmp_path / "a")
    ident = a.add(Image.new("RGB", (30, 20), "blue"))
    shared = tmp_path / "shared"
    sync_workspace(a, shared)
    original = a.path(ident).read_bytes()
    (shared / "media" / a.path(ident).name).write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        sync_workspace(a, shared)
    assert a.path(ident).read_bytes() == original
    assert not (shared / ".screenkaffee.lock").exists()
    (shared / ".screenkaffee.lock").write_text("another instance")
    with pytest.raises(RuntimeError):
        sync_workspace(a, shared)
    assert (shared / ".screenkaffee.lock").exists()
    a.close()


def test_mail_is_a_local_unsent_draft_and_color_formats(tmp_path):
    path = save_mail_draft(tmp_path / "draft.eml", [("Beispiel.png", b"flattened-image"), ("Clip.mp4", b"video")], subject="Aufnahmen für dich")
    message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    assert message["X-Unsent"] == "1" and message["Subject"] == "Aufnahmen für dich"
    assert not message.get("To")
    assert [p.get_payload(decode=True) for p in message.iter_attachments()] == [b"flattened-image", b"video"]
    assert color_formats("#0000ff") == {"HEX": "#0000FF", "RGB": "rgb(0, 0, 255)", "HSL": "hsl(240, 100%, 50%)", "HSV": "hsv(240, 100%, 100%)"}
    with pytest.raises(ValueError):
        color_formats("invalid")


def test_media_properties_and_restore_conflict_preflight(tmp_path):
    from screenkaffee.color import save_png
    from screenkaffee.media_properties import media_properties
    workspace = Workspace(tmp_path / "workspace")
    ident = workspace.add(Image.new("RGB", (40, 20), "green"))
    details = media_properties(workspace.path(ident), metadata={"backend": "Test"})
    assert details["Breite"] == 40 and details["Höhe"] == 20
    assert details["Farbprofil"] and details["Aufnahmemetadaten"] == {"backend": "Test"}
    project = workspace.project_path(ident)
    project.write_text('{}')
    workspace.remove(ident)
    project.write_text('existing conflict')
    with pytest.raises(ValueError):
        workspace.restore(ident)
    assert not workspace.get(ident)
    assert (workspace.root / "trash" / (ident + ".png")).exists()
    assert not (workspace.images / (ident + ".png")).exists()
    workspace.close()


def test_sync_workspace_propagates_deletions_to_recoverable_trash(tmp_path):
    a, b = Workspace(tmp_path / "a"), Workspace(tmp_path / "b")
    shared = tmp_path / "shared"
    ident = a.add(Image.new("RGB", (30, 20), "purple"), "Zum Löschen")
    sync_workspace(a, shared, propagate_deletions=True)
    sync_workspace(b, shared, propagate_deletions=True)
    assert b.get(ident) is not None
    a.remove(ident)
    sync_workspace(a, shared, propagate_deletions=True)
    result = sync_workspace(b, shared, propagate_deletions=True)
    assert result["deleted"] == 1
    assert b.get(ident) is None
    assert any(r["id"] == ident for r in b.trash_entries())
    a.close()
    b.close()
