"""Mail drafts are local files. This module never sends a message."""
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
import mimetypes


def save_mail_draft(destination, attachments, subject="screenkaffee-Aufnahmen", body="Anbei die ausgewählten Aufnahmen."):
    message = EmailMessage(policy=SMTP)
    message["Subject"] = subject
    message["X-Unsent"] = "1"
    message.set_content(body)
    for name, content in attachments:
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        main, sub = mime.split("/", 1)
        message.add_attachment(content, maintype=main, subtype=sub, filename=Path(name).name)
    path = Path(destination)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(message.as_bytes())
    temporary.replace(path)
    return path
