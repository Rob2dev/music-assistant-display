"""What the app does when you tap: the menu, the settings, the language list, the rooms, the idle tile."""
import datetime
import json
import os
import time
import unittest

from helpers import TMP, Base, make_app, m, needs_fonts, open_settings, state_file


def row_y(i):
    return m.SET_Y0 + i * m.SET_ROW_PITCH + 18


def saved():
    with open(state_file("settings.json")) as f:
        return json.load(f)


@needs_fonts
class Menu(Base):
    def tap_row(self, app, name):
        x0, y0, x1, y1 = m.POWER_ROWS[name]
        app.power_until = time.time() + 20
        app.handle_tap((x0 + x1) // 2, (y0 + y1) // 2, "power", None)

    def test_each_row_does_its_own_thing(self):
        for name, opens, command in (("settings", True, []), ("restart", False, ["restart"]),
                                     ("shutdown", False, ["shutdown"]), ("cancel", False, [])):
            app = make_app()
            self.tap_row(app, name)
            self.assertEqual(app.settings_until > time.time(), opens, name)
            self.assertEqual(app.ran, command, name)
            self.assertEqual(app.power_until, 0, name)

    def test_a_tap_beside_the_panel_closes_it_and_between_rows_keeps_it(self):
        app = make_app(power_until=time.time() + 20)
        app.handle_tap(10, 10, "power", None)
        self.assertEqual(app.power_until, 0)
        app = make_app(power_until=time.time() + 20)
        app.handle_tap(240, 123, "power", None)
        self.assertGreater(app.power_until, time.time())

    def test_opening_the_settings_resets_a_stale_language_list(self):
        app = make_app(set_picker=True)
        self.tap_row(app, "settings")
        self.assertFalse(app.set_picker)
        self.assertEqual(app.set_tab, "general")

    def test_a_failing_command_brings_the_player_back(self):
        app = make_app(demo=False)
        del app.run_power                                            # the real one
        app.cfg["shutdown_command"] = ["/nonexistent/command"]
        app.run_power("shutdown")
        for _ in range(50):
            if app.power_busy is None:
                break
            time.sleep(0.05)
        self.assertIsNone(app.power_busy)
        self.assertEqual(app.toast, m.tr("power_failed"))


@needs_fonts
class SettingsFlow(Base):
    def tap(self, app, i, part="row"):
        app.handle_tap({"minus": 300, "plus": 430, "row": 100}[part], row_y(i), "settings", None)

    def test_the_language_row_opens_the_list_instead_of_cycling(self):
        app = open_settings(make_app())
        app.cfg["language"] = "en"
        self.tap(app, 0)
        self.assertTrue(app.set_picker)
        self.assertEqual(app.cfg["language"], "en")

    def test_choosing_a_language_applies_and_saves_it(self):
        app = open_settings(make_app())
        self.tap(app, 0)
        app.handle_tap(350, m.SET_Y0 + 2 * 50 + 10, "settings", None)               # row 2, right column: Italiano
        self.assertEqual(app.cfg["language"], "it")
        self.assertFalse(app.set_picker)
        self.assertEqual(m.tr("tab_general"), "Generale")
        self.assertEqual(saved()["language"], "it")
        self.assertEqual(m.load_config()["language"], "it")                         # a restart picks it up

    def test_every_language_can_be_picked(self):
        app = open_settings(make_app())
        for i, code in enumerate(m.LANGUAGE_CODES):
            self.tap(app, 0)
            app.handle_tap((100, 350)[i % 2], m.SET_Y0 + (i // 2) * 50 + 10, "settings", None)
            self.assertEqual(app.cfg["language"], code)

    def test_closing_the_list_changes_nothing(self):
        app = open_settings(make_app())
        app.cfg["language"] = "en"
        self.tap(app, 0)
        app.handle_tap(450, 26, "settings", None)
        self.assertFalse(app.set_picker)
        self.assertEqual(app.cfg["language"], "en")

    def test_steppers_stay_within_their_range(self):
        app = open_settings(make_app(), "display")
        app.cfg["clock_after_min"] = 7
        self.tap(app, 3, "plus"); self.tap(app, 3, "plus"); self.tap(app, 3, "minus")
        self.assertEqual(app.cfg["clock_after_min"], 8)
        for _ in range(80):
            self.tap(app, 3, "plus")
        self.assertEqual(app.cfg["clock_after_min"], 60)
        for _ in range(80):
            self.tap(app, 3, "minus")
        self.assertEqual(app.cfg["clock_after_min"], 1)

    def test_night_mode_goes_down_to_off_and_up_to_the_maximum(self):
        app = open_settings(make_app(), "display")
        app.cfg["night_after_min"] = 60
        self.tap(app, 4, "plus")
        self.assertEqual(app.cfg["night_after_min"], 75)
        for _ in range(10):
            self.tap(app, 4, "minus")
        self.assertEqual(app.cfg["night_after_min"], 0)
        self.assertEqual(m.setting_text(m.settings_rows("display")[4], 0), "Off")
        for _ in range(30):
            self.tap(app, 4, "plus")
        self.assertEqual(app.cfg["night_after_min"], 240)

    def test_a_tap_between_the_stepper_buttons_does_nothing(self):
        app = open_settings(make_app(), "display")
        before = dict(app.cfg)
        app.handle_tap(372, row_y(3), "settings", None)
        self.assertEqual(app.cfg, before)

    def test_volume_step_cycles(self):
        app = open_settings(make_app())
        app.cfg["volume_step"] = 5
        seen = []
        for _ in range(5):
            self.tap(app, 1)
            seen.append(app.cfg["volume_step"])
        self.assertEqual(seen, [10, 1, 2, 5, 10])

    def test_the_switches_flip_and_tell_the_sendspin_client(self):
        app = open_settings(make_app(), "display")
        self.tap(app, 0)
        self.assertFalse(app.cfg["show_cover"])
        self.tap(app, 1)
        self.assertFalse(app.cfg["show_analyser"])
        self.assertFalse(app.ss.viz_enabled)                                          # the client drops the frames
        self.assertEqual(saved(), {"show_cover": False, "show_analyser": False})
        self.tap(app, 1)
        self.assertTrue(app.ss.viz_enabled)

    def test_reset_goes_back_to_config_json_and_removes_settings_json(self):
        app = open_settings(make_app(), "display")
        self.tap(app, 0); self.tap(app, 1)
        app.set_tab = "general"
        self.tap(app, 2)
        self.assertEqual(app.settings, {})
        self.assertTrue(app.cfg["show_cover"] and app.cfg["show_analyser"] and app.ss.viz_enabled)
        self.assertEqual(app.cfg["clock_after_min"], 7)                              # from config.json
        self.assertEqual(app.toast, m.tr("settings_reset_done"))
        self.assertFalse(os.path.exists(state_file("settings.json")))

    def test_a_failed_save_still_applies_the_setting_and_says_so(self):
        app = open_settings(make_app())
        app.cfg["state_dir"] = "/proc/no-such-dir"
        self.tap(app, 1)
        self.assertEqual(app.toast, m.tr("settings_failed"))
        self.assertEqual(app.cfg["volume_step"], 10)

    def test_tabs_switch_and_the_close_button_closes(self):
        app = open_settings(make_app())
        tabs = m.tab_ranges(m.SET_TAB_NAMES)
        for name, (x0, x1) in tabs.items():
            app.handle_tap((x0 + x1) // 2, 26, "settings", None)
            self.assertEqual(app.set_tab, name)
        app.handle_tap(450, 26, "settings", None)
        self.assertEqual(app.settings_until, 0)

    def test_the_info_tab_changes_nothing(self):
        app = open_settings(make_app(), "info")
        before = dict(app.cfg)
        app.handle_tap(100, row_y(0), "settings", None)
        self.assertEqual(app.cfg, before)


def player(**kw):
    return {"id": kw.get("id", "x"), "name": kw.get("name", "Kitchen"), "member": kw.get("member", False),
            "available": kw.get("available", True)}


class FakeMA:
    def __init__(self, rows):
        self.rooms = {"leader": {"id": "L", "name": "Pi player"}, "rows": rows}
        self.refreshed, self.changes = [], []

    def snapshot(self):
        return {"stations": {"fav": [], "all": []}, "loading": False, "error": None, "loaded": True,
                "rooms": self.rooms, "rooms_loading": False, "rooms_error": None}

    def refresh_rooms_async(self, force=False, **kw):
        self.refreshed.append(force)

    def set_member_async(self, pid, add, on_done, on_retry=None):
        self.changes.append((pid, add))
        on_done(True)


@needs_fonts
class Rooms(Base):
    ROWS = [player(id="A", name="Living room", member=True), player(id="B", name="Kitchen"),
            player(id="C", name="Bedroom", available=False), player(id="D", name="Office"),
            player(id="E", name="Attic")]

    def app(self):
        return make_app(ma=FakeMA(list(self.ROWS)), st_tab="rooms", stations_until=time.time() + 30)

    def row(self, i):
        return m.ROOM_Y0 + i * m.ROOM_PITCH + 20

    def test_tapping_a_row_asks_first(self):
        app = self.app()
        app.handle_tap(240, self.row(1), "stations", None)
        self.assertEqual(app.room_confirm, {"id": "B", "name": "Kitchen", "add": True})
        app.handle_tap(240, 200, "room_confirm", None)                               # between yes and no: stays open
        self.assertIsNotNone(app.room_confirm)
        self.assertEqual(app.ma.changes, [])

    def test_yes_changes_the_group_and_no_does_not(self):
        app = self.app()
        app.handle_tap(240, self.row(1), "stations", None)
        app.handle_tap(150, 200, "room_confirm", None)                               # yes
        self.assertEqual(app.ma.changes, [("B", True)])
        self.assertIn("Kitchen", app.toast)
        app = self.app()
        app.handle_tap(240, self.row(1), "stations", None)
        app.handle_tap(320, 200, "room_confirm", None)                               # no
        self.assertEqual(app.ma.changes, [])
        self.assertIsNone(app.room_confirm)

    def test_a_member_is_removed_not_added(self):
        app = self.app()
        app.handle_tap(240, self.row(0), "stations", None)
        self.assertFalse(app.room_confirm["add"])
        app.handle_tap(150, 200, "room_confirm", None)
        self.assertEqual(app.ma.changes, [("A", False)])

    def test_tapping_beside_the_panel_cancels(self):
        app = self.app()
        app.handle_tap(240, self.row(0), "stations", None)
        app.handle_tap(10, 10, "room_confirm", None)
        self.assertIsNone(app.room_confirm)
        self.assertEqual(app.ma.changes, [])

    def test_an_offline_player_and_the_gaps_are_not_tappable(self):
        app = self.app()
        app.handle_tap(240, self.row(2), "stations", None)
        self.assertIsNone(app.room_confirm)
        app.handle_tap(240, m.ROOM_Y0 + m.ROOM_ROW_H + 2, "stations", None)
        self.assertIsNone(app.room_confirm)

    def test_the_second_page_starts_with_the_fifth_player(self):
        app = self.app()
        right = (m.ST_HEADER["right"][0] + m.ST_HEADER["right"][1]) // 2
        app.handle_tap(right, 26, "stations", None)
        app.handle_tap(240, self.row(0), "stations", None)
        self.assertEqual(app.room_confirm["id"], "E")

    def test_opening_the_tab_forces_a_refresh(self):
        app = self.app()
        app.st_tab = "fav"
        x0, x1 = m.tab_ranges(m.ST_TAB_NAMES)["rooms"]
        app.handle_tap((x0 + x1) // 2, 26, "stations", None)
        self.assertEqual(app.st_tab, "rooms")
        self.assertEqual(app.ma.refreshed, [True])


@needs_fonts
class IdleTile(Base):
    def test_the_tile_resumes_the_last_station_and_nothing_else_does(self):
        last = {"uri": "library://radio/1", "name": "Jazz"}
        app = make_app(ma=type("MA", (), {"last_played": lambda s: last})())
        app.idle.render(datetime.datetime(2026, 10, 21, 18, 42), last, None, False, t=0.0)
        resumed = []
        app.resume = lambda l: resumed.append(l["name"])
        box = app.idle.tile_box
        app.handle_tap((box[0] + box[2]) // 2, (box[1] + box[3]) // 2, "clock", None)
        self.assertEqual(resumed, ["Jazz"])
        app.handle_tap(100, 100, "clock", None)
        app.handle_tap(box[0] - 30, (box[1] + box[3]) // 2, "clock", None)
        self.assertEqual(resumed, ["Jazz"])
        self.assertGreater(app.awake_until, time.time())                             # any tap wakes the player screen


if __name__ == "__main__":
    unittest.main()
