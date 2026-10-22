"""Choosing and drawing the screen that is current."""
import datetime
import time

from PIL import ImageDraw

from ..i18n import current_language, tr
from ..settings import system_info


class Frames:
    def frame(self):
        data, err, cover = self.snapshot()
        if not self.cfg["show_cover"]:
            cover = None  # plain background, placeholder instead of the cover
        now = time.time()
        if (data or {}).get("state") == "playing":
            self.last_active = now
            # Remember what is playing: on every new track, and now and then (same title, other station).
            title = data["attributes"].get("media_title")
            if self.ma and (title != self.remembered[0] or now - self.remembered[1] > 120):
                self.remembered = (title, now)
                self.ma.remember_async()
        if self.toast and now > self.toast_until:
            self.toast = None
        if self.pressed is not None and now > self.pressed_until:
            self.pressed = None
        if self.power_busy:
            return (self.r.message(tr("power_busy_" + self.power_busy), [tr("power_wait_" + self.power_busy)]),
                    "power_busy", data)
        if now < self.settings_until:
            st = self.sock.snapshot() if not self.demo else None
            info = system_info(bool(st and "connected" in (st.get("server") or "")))
            if self.set_picker:
                return self.r.language_picker(current_language(), self.pressed if isinstance(self.pressed, int) else None), \
                    "settings", data
            return self.r.settings_screen(self.cfg, self.set_tab, self.pressed, info, self.toast), "settings", data
        idle_long = now - self.last_active > self.cfg["clock_after_min"] * 60
        self.idle_anim = None
        if idle_long and now > self.awake_until and not self.demo:
            night = bool(self.cfg["night_after_min"]) and now - self.last_active > self.cfg["night_after_min"] * 60
            if self.cfg["idle_style"] == "digital" and not night:
                return self.r.clock(data, err, self.toast), "clock", data
            last = self.ma.last_played() if self.ma else None
            logo = self.ma.last_logo_for() if self.ma else None
            img = self.idle.render(datetime.datetime.now(), last, logo, night)
            if err or self.toast:
                img = img.copy()  # the cached picture must stay clean
                self.r._overlay(ImageDraw.Draw(img), err, self.toast)
            if self.idle.animated:
                self.idle_anim = self.idle
            return img, "clock", data
        if self.ma and now < self.stations_until:
            snap = self.ma.snapshot()
            if self.st_tab is None:
                self.st_tab = "fav" if snap["stations"]["fav"] else "all"
            if self.st_tab == "rooms":
                self.ma.refresh_rooms_async()  # keep the list current while it is on screen
            a = (data or {}).get("attributes", {})
            # For radio the station name is in the album field (or the title when there is no track).
            current = a.get("media_album_name") or a.get("media_title")
            pressed = self.pressed if isinstance(self.pressed, int) else None
            img, self.st_page = self.r.stations(snap, self.st_tab, self.st_page, current, pressed, self.toast,
                                                cover=cover)
            if self.room_confirm and now < self.room_confirm_until:
                c = self.room_confirm
                return (self.r.room_confirm(img, c["name"], c["add"], self.pressed if isinstance(self.pressed, str)
                                            else None), "room_confirm", data)
            self.room_confirm = None
            return img, "stations", data
        self.room_confirm = None
        img = self.r.player(data, cover, err, self.pressed, self.toast, show_stations=self.ma is not None,
                            show_viz=self.cfg["show_analyser"], show_cover=self.cfg["show_cover"])
        if now < self.power_until:
            return self.r.power_menu(img, self.pressed), "power", data
        if now < self.popup_until:
            return self.r.volume_popup(img, data, self.pressed), "volume", data
        return img, "player", data


__all__ = ["Frames"]
