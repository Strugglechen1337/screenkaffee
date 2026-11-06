"""Conservative vertical screenshot stitching; reject ambiguous overlaps."""
import numpy as np
from PIL import Image


def overlap_rows(top, bottom, min_overlap=32, max_error=8.0):
    if top.width != bottom.width:
        raise ValueError("Scrollaufnahmen müssen dieselbe Breite haben.")
    a = np.asarray(top.convert("RGB"), dtype=np.float32)
    b = np.asarray(bottom.convert("RGB"), dtype=np.float32)
    maximum = min(top.height, bottom.height) - 1
    if maximum < min_overlap:
        raise ValueError("Bilder sind zu klein für eine zuverlässige Überlappung.")
    # Use a thin central band, excluding scrollbar/chrome at left/right edges.
    left, right = top.width // 10, top.width - top.width // 10
    stride = max(1, (right - left) // 160)
    a, b = a[:, left:right:stride], b[:, left:right:stride]
    candidates = []
    for rows in range(min_overlap, maximum + 1):
        segment = a[-rows:]
        # Flat regions have no alignment evidence.
        if float(np.std(segment, axis=0).mean()) < 3:
            continue
        error = float(np.mean(np.abs(segment - b[:rows])))
        candidates.append((error, rows))
    if not candidates:
        raise ValueError("Keine eindeutige Überlappung; mehr Bildinhalt überlappen lassen.")
    candidates.sort()
    error, rows = candidates[0]
    if error > max_error:
        raise ValueError(f"Überlappung nicht zuverlässig (Abweichung {error:.1f}). Feste Kopfzeilen ggf. zuschneiden.")
    alternatives = [e for e, n in candidates[1:] if abs(n - rows) > 4]
    if alternatives and min(alternatives) <= error + 1:
        raise ValueError("Mehrdeutige Überlappung (wiederholtes Muster). Bilder manuell zuschneiden.")
    return rows


def stitch_vertical(images):
    if len(images) < 2:
        raise ValueError("Mindestens zwei Aufnahmen in Scrollreihenfolge auswählen.")
    result = images[0].convert("RGB")
    previous = result
    for next_image in images[1:]:
        rows = overlap_rows(previous, next_image)
        extra = next_image.height - rows
        if (result.height + extra) * result.width > 150_000_000:
            raise ValueError("Ergebnis überschreitet 150 Megapixel.")
        canvas = Image.new("RGB", (result.width, result.height + extra))
        canvas.paste(result)
        canvas.paste(next_image.crop((0, rows, next_image.width, next_image.height)), (0, result.height))
        result, previous = canvas, next_image
    return result


def scroll_body(image, direction="vertical", header=0, footer=0):
    if direction not in ("vertical", "horizontal"):
        raise ValueError("Unbekannte Scrollrichtung.")
    work = image.transpose(Image.Transpose.TRANSPOSE) if direction == "horizontal" else image
    if header < 0 or footer < 0 or header + footer >= work.height - 32:
        raise ValueError("Feste Leisten lassen zu wenig überlappenden Bildinhalt übrig.")
    return work, work.crop((0, header, work.width, work.height - footer))


def restore_scroll(body, first, last, direction, header, footer):
    first, _ = scroll_body(first, direction, header, footer)
    last, _ = scroll_body(last, direction, header, footer)
    if body.width * (body.height + header + footer) > 150_000_000:
        raise ValueError("Ergebnis überschreitet 150 Megapixel.")
    result = Image.new("RGB", (body.width, body.height + header + footer))
    result.paste(first.crop((0, 0, first.width, header)), (0, 0))
    result.paste(body, (0, header))
    if footer:
        result.paste(last.crop((0, last.height - footer, last.width, last.height)), (0, header + body.height))
    return result.transpose(Image.Transpose.TRANSPOSE) if direction == "horizontal" else result


def stitch_scroll(images, direction="vertical", header=0, footer=0):
    if not images:
        raise ValueError("Keine Scrollbilder vorhanden.")
    bodies = [scroll_body(image, direction, header, footer)[1] for image in images]
    body = stitch_vertical(bodies) if len(bodies) > 1 else bodies[0]
    return restore_scroll(body, images[0], images[-1], direction, header, footer)

