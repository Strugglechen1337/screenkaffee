"""Local, recoverable output zoom controlled while a recording is running."""
from dataclasses import dataclass, asdict
import math


@dataclass(frozen=True)
class ZoomEvent:
    seconds: float
    factor: float
    x: float = .5
    y: float = .5

    def __post_init__(self):
        if not all(math.isfinite(v) for v in (self.seconds, self.factor, self.x, self.y)):
            raise ValueError("Zoomwerte müssen endlich sein.")
        if self.seconds < 0 or not 1 <= self.factor <= 8 or not 0 <= self.x <= 1 or not 0 <= self.y <= 1:
            raise ValueError("Ungültiger Zoomzeitpunkt, Faktor oder Bildausschnitt.")


def zoom_expression(events, field, initial):
    expression, previous = f"{initial:.9g}", initial
    for event in events:
        value = getattr(event, field)
        delta = value - previous
        if delta:
            expression += f"+({delta:.9g})*gte(in_time,{event.seconds:.9g})"
        previous = value
    return expression


def apply_video_zoom(source, destination, events):
    from .media import probe_media, _render, _codec_options
    events = [e if isinstance(e, ZoomEvent) else ZoomEvent(**e) for e in events]
    if any(b.seconds < a.seconds for a, b in zip(events, events[1:])):
        raise ValueError("Zoomereignisse müssen zeitlich sortiert sein.")
    if len(events) > 1000:
        raise ValueError("Zu viele Zoomwechsel; die Originalaufnahme bleibt erhalten.")
    info = probe_media(source)
    width, height = info.width + info.width % 2, info.height + info.height % 2
    z = zoom_expression(events, "factor", 1)
    x = zoom_expression(events, "x", .5)
    y = zoom_expression(events, "y", .5)
    filters = (f"pad={width}:{height},zoompan=z='{z}':"
               f"x='max(0,min(iw-iw/zoom,iw*({x})-iw/zoom/2))':"
               f"y='max(0,min(ih-ih/zoom,ih*({y})-ih/zoom/2))':"
               f"d=1:s={width}x{height}:fps={info.fps or 30:.9g},setsar=1")
    return _render(["-i", str(source), "-map", "0:v:0", "-map", "0:a?", "-vf", filters,
                    *_codec_options(".mp4")], destination, [source])


def serialize_events(events):
    return [asdict(event) for event in events]
