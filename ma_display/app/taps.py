"""Handling taps and long presses, screen by screen, and changing settings."""
import time

from ..i18n import set_language, tr
from ..layout import (
    POPUP, POPUP_SECS, POWER_PANEL, POWER_SECS, POWER_ZONE, ROOM_CONFIRM, ROOM_ROWS, SETTINGS_SECS,
    STATIONS_SECS, ST_COLS, ST_ROWS, button_at, confirm_button_at, picker_hit, popup_bar_value,
    popup_button_at, power_button_at, rooms_row_at, settings_hit, stations_hit,
)
from ..settings import LANGUAGE_CODES, SETTING_KEYS, save_settings, settings_rows


class Taps:
    def handle_hold(self, x, y, screen):
        """A long press: on the clock of the player screen it opens the power menu."""
        now = time.time()
        self.awake_until = now + 30
        zx0, zy0, zx1, zy1 = POWER_ZONE
        if screen == "player" and zx0 <= x < zx1 and zy0 <= y < zy1:
            self.power_until = now + POWER_SECS

    def handle_tap(self, x, y, screen, data):
        now = time.time()
        self.awake_until = now + 30
        if screen == "power_busy":
            return
        if screen == "clock":
            # the "last station" tile resumes it; any other tap just wakes the player screen
            box, last = self.idle.tile_box, (self.ma.last_played() if self.ma else None)
            if box and last and box[0] <= x < box[2] and box[1] <= y < box[3]:
                self.resume(last)
            return
        if screen == "settings":
            self.settings_tap(x, y)
            return
        if screen == "power":
            btn = power_button_at(x, y)
            px0, py0, px1, py1 = POWER_PANEL
            if btn is None:
                # beside the panel: close; on the panel but between buttons: keep it open
                self.power_until = now + POWER_SECS if (px0 <= x < px1 and py0 <= y < py1) else 0
                return
            self.pressed, self.pressed_until = btn, now + 0.25
            self.power_until = 0
            if btn == "settings":
                self.settings_until, self.set_tab, self.set_picker = now + SETTINGS_SECS, "general", False
            elif btn != "cancel":
                self.run_power(btn)
            return
        if screen == "room_confirm":
            self.stations_until = now + STATIONS_SECS
            btn = confirm_button_at(x, y)
            px0, py0, px1, py1 = ROOM_CONFIRM
            if btn is None:
                if not (px0 <= x < px1 and py0 <= y < py1):
                    self.room_confirm = None  # tapped beside the panel: cancel
                return
            self.pressed, self.pressed_until = btn, now + 0.25
            c, self.room_confirm = self.room_confirm, None
            if btn == "yes" and c:
                self.change_member(c)
            return
        if screen == "stations":
            self.stations_tap(x, y)
            return
        if screen == "volume":
            x0, y0, x1, y1 = POPUP
            if not (x0 <= x < x1 and y0 <= y < y1):
                self.popup_until = 0  # tapped beside the panel: close
                return
            self.popup_until = now + POPUP_SECS
            value = popup_bar_value(x, y)
            if value is not None:
                self.action("vol_set", data, value)
                return
            btn = popup_button_at(x, y)
            if btn:
                self.action(btn, data)
            return
        btn = button_at(x, y)
        if btn == "volume":
            self.pressed, self.pressed_until = btn, now + 0.25
            self.popup_until = now + POPUP_SECS
        elif btn == "stations":
            if self.ma:
                self.stations_until = now + STATIONS_SECS
                self.st_tab, self.st_page = None, 0
                self.ma.refresh_async()
        elif btn:
            self.action(btn, data)

    def stations_tap(self, x, y):
        now = time.time()
        self.stations_until = now + STATIONS_SECS
        hit = stations_hit(x, y)
        if not hit:
            return
        rooms = self.st_tab == "rooms"
        items = self.ma.snapshot()["rooms"]["rows"] if rooms else self.ma.snapshot()["stations"].get(self.st_tab) or []
        per = ROOM_ROWS if rooms else ST_COLS * ST_ROWS
        if hit[0] == "close":
            self.stations_until = 0
        elif hit[0] == "tab":
            self.st_tab, self.st_page = hit[1], 0
            if hit[1] == "rooms":
                self.ma.refresh_rooms_async(force=True)
        elif hit[0] == "left":
            self.st_page = max(0, self.st_page - 1)
        elif hit[0] == "right":
            self.st_page = min(max(0, (len(items) - 1) // per), self.st_page + 1)
        elif hit[0] == "tile" and rooms:
            self.rooms_tap(x, y, items, per)
        elif hit[0] == "tile":
            idx = self.st_page * per + hit[1]
            if idx >= len(items):
                if not items and self.ma.snapshot()["error"]:
                    self.ma.refresh_async(force=True)  # the list failed to load: a tap retries
                return
            station = items[idx]
            self.pressed, self.pressed_until = idx, now + 0.4

            def done(ok):
                self.show_toast(station["name"] if ok else tr("play_failed"), 2.5)
                if ok:
                    self.stations_until = 0
                    self.sock.wake.set()
                self.dirty.set()

            self.ma.play_async(station, done, self.retrying)

    def rooms_tap(self, x, y, items, per):
        """A tap in the rooms list: ask to add/remove that player (an offline one cannot be added)."""
        row_in_page = rooms_row_at(x, y)
        idx = None if row_in_page is None else self.st_page * per + row_in_page
        if idx is None or idx >= len(items):
            if not items and self.ma.snapshot()["rooms_error"]:
                self.ma.refresh_rooms_async(force=True)  # the list failed to load: a tap retries
            return
        row = items[idx]
        if not row["available"]:
            return
        now = time.time()
        self.pressed, self.pressed_until = idx, now + 0.4
        self.room_confirm = {"id": row["id"], "name": row["name"], "add": not row["member"]}
        self.room_confirm_until = now + 15

    def settings_tap(self, x, y):
        now = time.time()
        self.settings_until = now + SETTINGS_SECS
        if self.set_picker:
            pick = picker_hit(x, y)
            if pick == "close":
                self.set_picker = False
            elif pick is not None:
                self.pressed, self.pressed_until = pick, now + 0.25
                self.apply_setting("language", LANGUAGE_CODES[pick])
                self.set_picker = False
            return
        hit = settings_hit(x, y, len(settings_rows(self.set_tab)))
        if hit is None:
            return
        if hit[0] == "close":
            self.settings_until = 0
            self.set_picker = False
        elif hit[0] == "tab":
            self.set_tab = hit[1]
        elif self.set_tab in ("general", "display"):
            sd = settings_rows(self.set_tab)[hit[1]]
            self.pressed, self.pressed_until = hit[1], now + 0.25
            if sd["kind"] == "action":
                self.reset_settings()
                return
            cur = self.cfg[sd["key"]]
            if sd["kind"] == "toggle":
                self.apply_setting(sd["key"], not cur)
                return
            if sd["key"] == "language":
                self.set_picker = True  # too many languages to cycle through: open the list
                return
            if sd["kind"] == "cycle":
                values = sd["values"]
                new = values[(values.index(cur) + 1) % len(values)] if cur in values else values[0]
            elif hit[2] == "row":
                return
            else:
                step = sd["step"] * (-1 if hit[2] == "minus" else 1)
                new = min(sd["max"], max(sd["min"], round((cur + step) / sd["step"]) * sd["step"]))
            self.apply_setting(sd["key"], new)

    def _sync_viz(self):
        """Tell the Sendspin client whether the analyser is wanted (it then drops the spectrum frames)."""
        if not self.demo:
            self.ss.viz_enabled = self.cfg["show_analyser"]

    def apply_setting(self, key, value):
        """Change a setting now and remember it across restarts."""
        self.cfg[key] = self.settings[key] = value
        if key == "language":
            set_language(value)
        self._sync_viz()
        if not save_settings(self.cfg["state_dir"], self.settings):
            self.show_toast(tr("settings_failed"), 3)

    def reset_settings(self):
        self.settings = {}
        for key in SETTING_KEYS:
            self.cfg[key] = self.base_cfg[key]
        set_language(self.cfg["language"])
        self._sync_viz()
        self.show_toast(tr("settings_reset_done") if save_settings(self.cfg["state_dir"], {})
                        else tr("settings_failed"), 2.5)


__all__ = ["Taps"]
