#!/usr/bin/env python3
"""Render sample screens of the display into docs/screenshots/.

Everything is drawn by the program's own renderer from made-up data (no real covers, station logos or
accounts), so the pictures can be regenerated at any time. Needs Pillow and the DejaVu fonts, e.g. on the Pi:

    python3 tools/screenshots.py
"""
import datetime
import math
import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import ma_display as m  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

OUT = os.environ.get("SCREENSHOT_DIR", os.path.join(HERE, "..", "docs", "screenshots"))
NOW = datetime.datetime(2026, 10, 21, 21, 45, 20)


class FixedDateTime(datetime.datetime):
    """Makes the clock in the pictures read 21:45."""

    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.replace(tzinfo=tz)


def cover(c1, c2):
    """Made-up album art: a vertical gradient with a ring."""
    img = Image.new("RGB", (400, 400))
    d = ImageDraw.Draw(img)
    for y in range(400):
        t = y / 399
        d.line([(0, y), (400, y)], fill=tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)))
    d.ellipse((90, 90, 310, 310), outline=(255, 235, 200), width=14)
    d.ellipse((165, 165, 235, 235), fill=(255, 235, 200))
    return img


def logo(text, color, size=None):
    """Made-up station logo: initials on a coloured square."""
    size = size or m.MAClient.LOGO
    img = Image.new("RGB", (size, size), color)
    ImageDraw.Draw(img).text((size // 2, size // 2), text, font=m.font(int(size * 0.36), True), fill=(255, 255, 255),
                             anchor="mm")
    return img


def with_spectrum(r, img):
    """Paste a sample spectrum into the analyser area (it is animated in the real program)."""
    if r.viz_box:
        viz = m.SpectrumViz(m.VIZ_BINS)
        viz.levels = [min(1.0, 0.12 + 0.85 * abs(math.sin(i * 0.5 + 1.0)) * (1 - i / 34)) for i in range(m.VIZ_BINS)]
        img.paste(viz.render(img, r.viz_box, r.accent), r.viz_box[:2])
    return img


def main():
    m.set_language("en")
    os.makedirs(OUT, exist_ok=True)
    shots = {}

    music = {"state": "playing", "attributes": {
        "media_title": "Midnight Drive", "media_artist": "The Example Band", "media_album_name": "City Lights",
        "media_duration": 214, "media_position": 97, "volume_level": 0.6}}
    radio = {"state": "playing", "attributes": {
        "media_title": "Late Night Jazz", "media_artist": "Smooth Quartet", "media_album_name": "Jazz Lounge",
        "media_duration": 0, "volume_level": 0.6}}
    jazz = logo("JL", (150, 70, 160), 184)
    stations = [("Jazz Lounge", (150, 70, 160)), ("Sunrise FM", (230, 130, 40)), ("Rock Radio", (190, 50, 50)),
                ("Classic Hits", (60, 110, 180)), ("Chill Out", (40, 150, 140)), ("News 24", (90, 90, 100))]
    items = [{"name": n, "uri": "library://radio/%d" % i, "logo": logo("".join(w[0] for w in n.split()[:2]), c)}
             for i, (n, c) in enumerate(stations, 1)]
    snap = {"stations": {"fav": items, "all": items}, "loading": False, "error": None, "loaded": True,
            "rooms": {"leader": {"id": "L", "name": "Pi player"}, "rows": [
                {"id": "1", "name": "Living room", "member": True, "available": True},
                {"id": "2", "name": "Kitchen", "member": False, "available": True},
                {"id": "3", "name": "Office", "member": False, "available": True},
                {"id": "4", "name": "Bedroom", "member": False, "available": False}]},
            "rooms_loading": False, "rooms_error": None}
    last = {"uri": "library://radio/1", "name": "Jazz Lounge"}

    with mock.patch.object(datetime, "datetime", FixedDateTime):
        r_music = m.Renderer()
        shots["player-music"] = r_music.player(music, cover((70, 40, 120), (230, 110, 90)), None, show_stations=True)
        r = m.Renderer()
        shots["player-radio"] = with_spectrum(r, r.player(radio, jazz, None, show_stations=True))
        r = m.Renderer()
        shots["player-no-cover"] = with_spectrum(r, r.player(radio, None, None, show_stations=True, show_cover=False))
        shots["menu"] = r_music.power_menu(shots["player-music"])  # the menu blurs the screen it sits on
        r = m.Renderer()
        shots["stations"] = r.stations(snap, "fav", 0, "Jazz Lounge", None, None)[0]
        shots["rooms"] = m.Renderer().stations(snap, "rooms", 0, "", None, None)[0]
        ic = m.IdleClock()
        idle = ic.render(NOW, last, jazz, False, t=0.0).copy()
        idle.paste(ic.hands(NOW), ic.face_box[:2])
        shots["idle"] = idle
        shots["idle-night"] = m.IdleClock().render(NOW, None, None, True, t=0.0)
        cfg = dict(m.DEFAULTS, language="en")
        shots["settings"] = m.Renderer().settings_screen(cfg, "display", None, [])

    for name, img in shots.items():
        img.save(os.path.join(OUT, name + ".png"), optimize=True)

    # One overview picture for the top of the README: 3 x 3, each frame at 2/3 size.
    order = ["player-music", "player-radio", "player-no-cover", "stations", "rooms", "idle", "idle-night", "menu",
             "settings"]
    tw, th, pad = 320, 213, 8
    sheet = Image.new("RGB", (3 * tw + 4 * pad, 3 * th + 4 * pad), (24, 24, 30))
    for i, name in enumerate(order):
        x, y = pad + (i % 3) * (tw + pad), pad + (i // 3) * (th + pad)
        sheet.paste(shots[name].resize((tw, th), Image.LANCZOS), (x, y))
    sheet.save(os.path.join(OUT, "overview.png"), optimize=True)
    print("wrote %d pictures to %s" % (len(shots) + 1, os.path.abspath(OUT)))


if __name__ == "__main__":
    main()
