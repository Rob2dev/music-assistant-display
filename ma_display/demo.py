"""Sample data for --demo."""
import datetime
import time

from PIL import Image, ImageDraw


# ---------------------------------------------------------------- app

def _iso_at(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()


DEMO_STATE = {
    "state": "playing",
    "attributes": {
        "media_title": "Bohemian Rhapsody", "media_artist": "Queen",
        "media_album_name": "A Night at the Opera (2011 Remaster)",
        "media_duration": 355, "media_position": 121, "media_position_updated_at": _iso_at(time.time()),
        "volume_level": 0.76, "is_volume_muted": False,
    },
}


def demo_cover():
    img = Image.new("RGB", (300, 300))
    d = ImageDraw.Draw(img)
    for y in range(300):
        d.line([(0, y), (300, y)], fill=(120 + y // 3, 40 + y // 5, 90 - y // 6))
    d.ellipse((60, 60, 240, 240), outline=(255, 220, 160), width=10)
    return img


__all__ = [
    "_iso_at", "DEMO_STATE", "demo_cover",
]
