"""Rendering: every screen draws without errors at the right size, and the pieces that decide layout behave.

These are smoke and geometry tests, not pixel comparisons: the pictures differ a little between fonts and Pillow
versions. (`tools/screenshots.py` draws the screens to look at.)
"""
import datetime
import unittest

from helpers import Base, m, needs_fonts, station

NOW = datetime.datetime(2026, 10, 21, 21, 45, 20)
SIZE = (m.W, m.H)
MUSIC = {"state": "playing", "attributes": {"media_title": "Midnight Drive", "media_artist": "The Example Band",
         "media_album_name": "City Lights", "media_duration": 214, "media_position": 97, "volume_level": 0.6}}
RADIO = {"state": "playing", "attributes": {"media_title": "Late Night Jazz", "media_artist": "Smooth Quartet",
         "media_album_name": "Jazz Lounge", "media_duration": 0, "volume_level": 0.6}}
TALL = {"state": "playing", "attributes": {   # a title on two lines, an artist and an album: no room for the analyser
    "media_title": "Across the Wide World", "media_artist": "The Example Band",
    "media_album_name": "A Night at the Opera (2011 Remaster)", "media_duration": 214, "media_position": 97}}
LONG = {"state": "playing", "attributes": {
    "media_title": "Concerto for Violin and Orchestra in D major, Op. 61: II. Larghetto",
    "media_artist": "Anne-Sophie Mutter, Berliner Philharmoniker & Herbert von Karajan", "media_duration": 700,
    "media_position": 30}}


def cover():
    from PIL import Image
    return Image.new("RGB", (200, 200), (120, 60, 90))


def snapshot(rows=(), leader=True, rooms_error=None, rooms_loading=False, error=None):
    items = [station("Station %d" % i, "library://radio/%d" % i) for i in range(1, 7)]
    return {"stations": {"fav": items, "all": items}, "loading": False, "error": error, "loaded": True,
            "rooms": {"leader": {"id": "L", "name": "Pi player"} if leader else None, "rows": list(rows)},
            "rooms_loading": rooms_loading, "rooms_error": rooms_error}


@needs_fonts
class PlayerScreen(Base):
    def test_every_state_draws(self):
        for lang in m.STRINGS:
            m.set_language(lang)
            for state in ("playing", "paused", "idle", "stopped", "off", "unavailable", "starting"):
                img = m.Renderer().player({"state": state, "attributes": {}}, None, None, show_stations=True)
                self.assertEqual(img.size, SIZE, (lang, state))

    def test_music_radio_and_long_titles(self):
        for data in (MUSIC, RADIO, LONG):
            for show_cover in (True, False):
                r = m.Renderer()
                img = r.player(data, cover() if show_cover else None, "some error", toast="a toast", show_stations=True,
                               show_cover=show_cover)
                self.assertEqual(img.size, SIZE)

    def test_a_long_title_scrolls(self):
        r = m.Renderer()
        r.player(LONG, None, None)
        self.assertTrue(r.marquees)
        r.player(MUSIC, None, None)
        self.assertEqual(r.marquees, [])

    def test_the_analyser_needs_room_and_the_switch(self):
        r = m.Renderer()
        r.player(RADIO, None, None)
        self.assertIsNotNone(r.viz_box)                                                # radio: room under the text
        r.player(RADIO, None, None, show_viz=False)
        self.assertIsNone(r.viz_box)                                                   # switched off
        r.player(MUSIC, cover(), None)
        self.assertEqual(r.viz_box[3], 164)                                            # above the progress bar of a track
        self.assertGreaterEqual(r.viz_box[3] - r.viz_box[1], 24)
        r.player(RADIO, cover(), None)
        self.assertEqual(r.viz_box[3], 200)                                            # radio: down to the bottom of the cover
        r.player(TALL, cover(), None)
        self.assertIsNone(r.viz_box)                                                   # the text takes the room: no analyser

    def test_without_a_cover_the_text_gets_the_full_width(self):
        r = m.Renderer()
        r.player(RADIO, None, None, show_cover=False)
        x0, y0, x1, y1 = r.viz_box
        self.assertEqual((x0, x1), (24, m.W - 24))
        r.player(RADIO, cover(), None, show_cover=True)
        self.assertGreaterEqual(r.viz_box[0], 216)

    def test_a_paused_player_has_no_analyser(self):
        r = m.Renderer()
        r.player({"state": "paused", "attributes": RADIO["attributes"]}, None, None)
        self.assertIsNone(r.viz_box)


@needs_fonts
class OtherScreens(Base):
    ROWS = [{"id": "1", "name": "Living room", "member": True, "available": True},
            {"id": "2", "name": "Bedroom", "member": False, "available": False}]

    def test_station_picker_and_rooms_in_every_language(self):
        for lang in m.STRINGS:
            m.set_language(lang)
            for tab in ("fav", "all", "rooms"):
                img, page = m.Renderer().stations(snapshot(self.ROWS), tab, 0, "Station 1")
                self.assertEqual(img.size, SIZE, (lang, tab))

    def test_empty_loading_and_error_states(self):
        for snap in (snapshot(), snapshot(leader=False, rooms_loading=True), snapshot(leader=False, rooms_error="x"),
                     snapshot(error="x")):
            for tab in ("fav", "rooms"):
                img, _ = m.Renderer().stations(snap, tab, 0, "")
                self.assertEqual(img.size, SIZE)

    def test_settings_tabs_and_the_language_list(self):
        cfg = dict(m.DEFAULTS)
        info = [("info_host", "pi"), ("info_ip", "10.0.0.2"), ("info_uptime", "1 d"), ("info_ma", "x"), ("info_temp", "40")]
        for lang in m.STRINGS:
            m.set_language(lang)
            r = m.Renderer()
            for tab in ("general", "display", "info"):
                self.assertEqual(r.settings_screen(cfg, tab, None, info).size, SIZE)
            self.assertEqual(r.language_picker(lang).size, SIZE)

    def test_menu_and_confirmation(self):
        r = m.Renderer()
        base = r.player(MUSIC, None, None)
        self.assertEqual(r.power_menu(base).size, SIZE)
        base = r.stations(snapshot(self.ROWS), "rooms", 0, "")[0]
        self.assertEqual(r.room_confirm(base, "Living room", False).size, SIZE)
        self.assertEqual(r.room_confirm(base, "Kitchen", True, pressed="yes").size, SIZE)

    def test_volume_popup_clock_and_message(self):
        r = m.Renderer()
        base = r.player(MUSIC, None, None)
        self.assertEqual(r.volume_popup(base, MUSIC).size, SIZE)
        self.assertEqual(r.clock(MUSIC, None).size, SIZE)
        self.assertEqual(r.message("Title", ["one line", "another"]).size, SIZE)


@needs_fonts
class IdleScreen(Base):
    LAST = {"uri": "library://radio/1", "name": "Jazz Lounge"}

    def test_everything_stays_on_screen_during_the_whole_drift(self):
        ic = m.IdleClock()
        for t in range(0, 4200, 7):
            ic.render(NOW, self.LAST, None, False, t=t)
            for box in (ic.face_box, ic.tile_box):
                x0, y0, x1, y1 = box
                self.assertTrue(0 <= x0 and 0 <= y0 and x1 <= m.W and y1 <= m.H, (t, box))
            self.assertGreater(ic.tile_box[0], ic.face_box[2])                       # the tile never touches the face

    def test_drift_is_slow(self):
        offsets = [m.IdleClock.offset(t) for t in range(3600)]
        step = max(max(abs(b[0] - a[0]), abs(b[1] - a[1])) for a, b in zip(offsets, offsets[1:]))
        self.assertEqual(step, 1)                                                      # never more than a pixel per second

    def test_the_picture_is_cached_until_something_changes(self):
        ic = m.IdleClock()
        a = ic.render(NOW, self.LAST, None, False, t=100.0)
        self.assertIs(ic.render(NOW, self.LAST, None, False, t=100.5), a)
        self.assertIsNot(ic.render(NOW, self.LAST, None, False, t=100.0 + 3600), a)    # the drift moved
        self.assertIsNot(ic.render(NOW, None, None, False, t=100.0), a)                 # the last station changed

    def test_hands_move_and_leave_the_cached_face_alone(self):
        ic = m.IdleClock()
        ic.render(NOW, self.LAST, None, False, t=0.0)
        before = ic.base.tobytes()
        a, b = ic.hands(NOW), ic.hands(NOW + datetime.timedelta(seconds=1))
        self.assertEqual(a.size, (ic.face_box[2] - ic.face_box[0],) * 2)
        self.assertNotEqual(a.tobytes(), b.tobytes())
        self.assertEqual(ic.base.tobytes(), before)

    def test_night_mode_has_no_hands_and_changes_every_minute(self):
        ic = m.IdleClock()
        a = ic.render(NOW, self.LAST, None, True, t=0.0)
        self.assertIsNone(ic.hands(NOW))
        self.assertFalse(ic.animated)
        self.assertIsNone(ic.tile_box)
        self.assertIsNot(ic.render(NOW + datetime.timedelta(minutes=1), self.LAST, None, True, t=0.0), a)

    def test_it_draws_in_every_language_with_and_without_a_last_station(self):
        for lang in m.STRINGS:
            m.set_language(lang)
            for last in (self.LAST, None):
                ic = m.IdleClock()
                self.assertEqual(ic.render(NOW, last, None, False, t=0.0).size, SIZE)


class Geometry(Base):
    def test_the_framebuffer_helpers(self):
        self.assertEqual(m.fmt_time(65), "1:05")
        self.assertEqual(m.fmt_time(3725), "1:02:05")
        self.assertEqual(m.fmt_time(-3), "0:00")
        self.assertIsNone(m.find_framebuffer("/dev/does-not-exist", ""))
        self.assertIsNone(m.find_framebuffer("auto", "a-driver-that-is-not-there"))


if __name__ == "__main__":
    unittest.main()
