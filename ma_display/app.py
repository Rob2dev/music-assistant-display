"""The application: merges state, handles taps and runs the main loop."""
import copy
import datetime
import logging
import math
import queue
import subprocess
import threading
import time

from PIL import ImageDraw

from .config import load_config
from .demo import DEMO_STATE, _iso_at, demo_cover
from .hardware import Touch, wait_for_route
from .i18n import current_language, set_language, tr
from .layout import (
    POPUP, POPUP_SECS, POWER_PANEL, POWER_SECS, POWER_ZONE, ROOM_CONFIRM, ROOM_ROWS, SETTINGS_SECS,
    STATIONS_SECS, ST_COLS, ST_ROWS, button_at, confirm_button_at, picker_hit, popup_bar_value,
    popup_button_at, power_button_at, rooms_row_at, settings_hit, stations_hit,
)
from .music_assistant import MAClient
from .player_socket import PlayerSocket
from .renderer import Renderer
from .sendspin import SendspinDisplay, VIZ_BINS, VIZ_FPS
from .settings import LANGUAGE_CODES, SETTING_KEYS, load_settings, save_settings, settings_rows, system_info
from .widgets import IdleClock, MARQUEE_FPS, SpectrumViz

log = logging.getLogger("ma-display")


def _blank(v):
    return v if v and v.lower() not in ("none", "-", "unknown", "(none)") else None


def same_track(meta, sock_title):
    """Does the Sendspin metadata belong to what sendspin-cli is playing? It sometimes reports 'ARTIST - Title'."""
    title = (meta.get("title") or "").casefold().strip()
    s = (sock_title or "").casefold().strip()
    if not title:
        return False
    return not s or s == title or s.endswith(" - " + title) or title in s


class App:
    # Seconds the player plays while the display shows something else, before it sends 'switch'.
    SWITCH_AFTER = 6
    # The service starts before sendspin-cli is up. For this long an unreachable player reads
    # as "Starting…" rather than as an error.
    STARTUP_SECS = 90

    def __init__(self, cfg, sink, demo=False):
        self.cfg = cfg
        self.sink = sink
        self.demo = demo
        self.r = Renderer()
        self.taps = queue.Queue()
        self.dirty = threading.Event()
        self.toast = None
        self.toast_until = 0
        self.pressed = None
        self.pressed_until = 0
        self.last_active = time.time()
        self.started = time.time()
        self.awake_until = 0
        self.popup_until = 0
        self.power_until = 0
        self.power_busy = None  # "shutdown" / "restart" once chosen
        self.idle = IdleClock()
        self.settings = load_settings(cfg["state_dir"])  # what was changed on screen (also in cfg)
        self.base_cfg = load_config(use_settings=False)  # what "reset to defaults" goes back to
        self.settings_until = 0
        self.set_tab = "general"
        self.set_picker = False  # the language list is open
        self.room_confirm = None  # {"id", "name", "add"} while the add/remove confirmation is open
        self.room_confirm_until = 0
        self.idle_anim = None  # the IdleClock while its hands have to be redrawn, else None
        self.stations_until = 0
        self.marquee_t0 = {}
        self.spectrum = SpectrumViz(VIZ_BINS)
        self.st_tab = None
        self.st_page = 0
        self.optimistic = {}
        self.optimistic_until = 0
        self.mismatch_since = None
        self.last_switch = 0
        self.remembered = (None, 0)  # (title, time) of the last remember_async
        self._demo_cover = demo_cover() if demo else None
        if not demo:
            self.sock = PlayerSocket(cfg["control_socket"], self.dirty.set)
            self.ss = SendspinDisplay(cfg, self.dirty.set)
            self.ss.viz_enabled = cfg["show_analyser"]
        # Picking stations needs an MA token.
        self.ma = MAClient(cfg, self.dirty.set) if cfg["ma_token"] and cfg["ma_url"] and not demo else None
        if cfg["ma_token"] and not cfg["ma_url"] and not demo:
            log.warning("ma_token is set but ma_url is not: station picker disabled")

    # -- merge state into a single dict (same shape as an HA media_player)
    def snapshot(self):
        """Returns (data, error label, cover)."""
        if self.demo:
            return copy.deepcopy(DEMO_STATE), None, self._demo_cover
        st = self.sock.snapshot()
        ss = self.ss.snapshot()
        if st is None:
            if time.time() - self.started < self.STARTUP_SECS:
                return {"state": "starting", "attributes": {}}, None, None
            return {"state": "unavailable", "attributes": {}}, tr("err_no_player"), None
        state = (st.get("state") or "idle").split()[0].lower()
        sock_title = _blank(st.get("track"))
        m = ss["meta"]
        rich = ss["connected"] and same_track(m, sock_title)
        a = {}
        if rich:
            a["media_title"] = m.get("title")
            a["media_artist"] = m.get("artist")
            a["media_album_name"] = m.get("album")
            if "duration_ms" in m:
                a["media_duration"] = m["duration_ms"] / 1000
                a["media_position"] = m["progress_ms"] / 1000
                a["media_position_updated_at"] = _iso_at(m["at"])
            cover = ss["cover"]
        else:
            a["media_title"] = sock_title
            cover = None
            # No Sendspin metadata yet (just started): for radio, show the remembered station name already.
            last = self.ma.last_played() if self.ma else None
            if sock_title and state == "playing" and last and "/radio/" in last["uri"]:
                a["media_album_name"] = last["name"]
        try:
            a["volume_level"] = int(st.get("group volume", "").split()[0]) / 100
        except (ValueError, IndexError):
            pass
        if ss["muted"] is not None and rich:
            a["is_volume_muted"] = ss["muted"]
        last = self.ma.last_played() if self.ma else None
        if last and not a.get("media_title"):
            a["resume_name"] = last["name"]
        if cover is None and last and "/radio/" in last["uri"]:
            # Station logo as cover until the real artwork arrives (or when the queue is empty).
            if not a.get("media_title") or a.get("media_album_name") == last["name"]:
                cover = self.ma.last_logo_for()
        data = {"state": state, "attributes": a}
        if self.optimistic and time.time() < self.optimistic_until:
            if "state" in self.optimistic:
                data["state"] = self.optimistic["state"]
            a.update(self.optimistic.get("attributes", {}))
        error = None
        if "connected" not in (st.get("server") or ""):
            error = tr("err_no_ma")
        if ss["pin"]:
            error = tr("err_pairing", pin=ss["pin"])
        self._maybe_switch(state, sock_title, ss)
        return data, error, cover

    def _maybe_switch(self, state, sock_title, ss):
        """Send the display to the player's group if it shows something else."""
        now = time.time()
        if not ss["connected"] or state != "playing":
            self.mismatch_since = None
            return
        mismatch = ss["group_state"] != "playing" or not same_track(ss["meta"], sock_title)
        if not mismatch:
            self.mismatch_since = None
            return
        self.mismatch_since = self.mismatch_since or now
        if now - self.mismatch_since >= self.SWITCH_AFTER and now - self.last_switch >= self.SWITCH_AFTER:
            log.info("display is not showing what the player plays (%r vs %r): switch",
                     ss["meta"].get("title"), sock_title)
            self.last_switch = now
            self.ss.switch()

    def show_toast(self, text, secs=2.0):
        self.toast, self.toast_until = text, time.time() + secs

    def action(self, btn, data, value=None):
        a = (data or {}).get("attributes", {})
        if btn == "vol_set":
            # Pick a volume directly on the bar; also unmutes.
            self.optimistic = {"attributes": {"volume_level": value / 100, "is_volume_muted": False}}
            self.optimistic_until = time.time() + 3
            try:
                if not self.demo:
                    for line in ([f"vol {value}"] + (["mute off"] if a.get("is_volume_muted") else [])):
                        ok, text = self.sock.cmd(line)
                        if not ok:
                            raise RuntimeError(text)
                    self.sock.wake.set()
            except Exception as ex:
                self.optimistic = {}
                self.show_toast(tr("failed"))
                log.warning("setting volume failed: %s", ex)
            return
        self.pressed, self.pressed_until = btn, time.time() + 0.25
        playing = (data or {}).get("state") == "playing"
        last = self.ma.last_played() if self.ma and not self.demo else None
        if btn == "play" and not playing and last and not a.get("media_title"):
            # Nothing in the queue: restart the last played item right away.
            self.resume(last)
            return
        try:
            if btn == "play":
                line = "pause" if playing else "play"
                self.optimistic = {"state": "paused" if playing else "playing"}
            elif btn in ("prev", "next"):
                line = btn
                self.optimistic = {}
            elif btn in ("vol-", "vol+"):
                cur = round((a.get("volume_level") or 0) * 100)
                vol = min(100, max(0, cur + self.cfg["volume_step"] * (1 if btn == "vol+" else -1)))
                line = f"vol {vol}"
                self.optimistic = {"attributes": {"volume_level": vol / 100}}
            elif btn == "mute":
                muted = bool(a.get("is_volume_muted"))
                line = "mute off" if muted else "mute on"
                self.optimistic = {"attributes": {"is_volume_muted": not muted}}
            else:
                return
            self.optimistic_until = time.time() + 3
            if not self.demo:
                ok, text = self.sock.cmd(line)
                if not ok:
                    raise RuntimeError(text)
                self.sock.wake.set()
                if line == "play" and last:
                    threading.Thread(target=self._resume_if_silent, args=(last,), daemon=True).start()
        except Exception as ex:
            self.optimistic = {}
            if btn == "play" and not playing and last:
                self.resume(last)
                return
            self.show_toast(tr("failed"))
            log.warning("action %s failed: %s", btn, ex)

    def resume(self, last):
        """Restart the last played station/track through MA (the queue is empty or not playing)."""
        self.optimistic = {"state": "playing"}
        self.optimistic_until = time.time() + 5

        def done(ok):
            if not ok:
                self.optimistic = {}
            self.show_toast(last["name"] if ok else tr("resume_failed"), 2.5)
            self.sock.wake.set()
            self.dirty.set()

        self.ma.play_async(last, done, self.retrying)

    def _resume_if_silent(self, last):
        """'play' on the socket does nothing if MA lost the queue: then start the last played item after all."""
        time.sleep(4)
        st = self.sock.snapshot() or {}
        if (st.get("state") or "").split()[:1] != ["playing"]:
            log.info("play did nothing, resuming %s", last["name"])
            self.resume(last)

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

    def change_member(self, c):
        def done(ok):
            self.show_toast(tr("room_done_add" if c["add"] else "room_done_remove", name=c["name"]) if ok
                            else tr("room_failed"), 2.5)
            self.dirty.set()

        self.ma.set_member_async(c["id"], c["add"], done, self.retrying)

    def retrying(self):
        self.show_toast(tr("retrying"), 2.5)
        self.dirty.set()

    def handle_hold(self, x, y, screen):
        """A long press: on the clock of the player screen it opens the power menu."""
        now = time.time()
        self.awake_until = now + 30
        zx0, zy0, zx1, zy1 = POWER_ZONE
        if screen == "player" and zx0 <= x < zx1 and zy0 <= y < zy1:
            self.power_until = now + POWER_SECS

    def run_power(self, kind):
        """Run the configured shutdown/restart command; on failure the player screen comes back."""
        cmd = self.cfg[kind + "_command"]
        if self.demo:
            log.info("demo: would run %s", cmd)
            self.show_toast(tr("power_failed"), 3)
            return
        self.power_busy = kind

        def run():
            try:
                subprocess.run(cmd, check=True, timeout=30, capture_output=True)
                log.info("%s: %s", kind, " ".join(cmd))
            except Exception as ex:
                log.warning("%s failed: %s", kind, getattr(ex, "stderr", None) or ex)
                self.power_busy = None
                self.show_toast(tr("power_failed"), 3)
            self.dirty.set()
        threading.Thread(target=run, daemon=True).start()

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

    def _start_touch(self):
        """Create the touch reader, retrying: with the early start the driver may not be up yet."""
        for attempt in range(120):
            try:
                Touch(self.cfg["touch_name"], self.cfg["calibration"], self.taps).start()
                return
            except Exception as e:
                if attempt == 0:
                    log.info("waiting for the touch device: %s", e)
                time.sleep(1)
        log.warning("touch not available")

    def _start_sendspin(self):
        """Start the Sendspin client once the network is up: it announces itself over mDNS, and an
        announcement made before there is an address would not be repeated."""
        if not wait_for_route():
            log.warning("no network after 120 s, starting the Sendspin client anyway")
        if self.ma:
            self.ma.refresh_async()  # the first attempt, at startup, usually ran before the network was up
        self.ss.start()

    def run(self, once=False):
        if not self.demo:
            self.sock.start()
            threading.Thread(target=self._start_sendspin, daemon=True).start()
            if self.ma:
                self.ma.refresh_async()  # fetch stations and logos up front
            time.sleep(0.5)  # wait for the first status
        if not once:
            threading.Thread(target=self._start_touch, daemon=True).start()
        last = None
        while True:
            img, screen, data = self.frame()
            on_player = screen == "player"
            marquees = self.r.marquees if on_player else []
            viz_box = self.r.viz_box if on_player else None
            idle = self.idle_anim if screen == "clock" else None
            if once:
                for strip, pos in self.animated_strips(img, marquees, viz_box, idle):
                    img.paste(strip, pos)
                self.sink.show(img)
                return
            b = img.tobytes()
            if b != last:
                # Put the moving parts (scrolling text, spectrum) into the full frame already;
                # otherwise they sit empty until the next strip update (flicker).
                shown = img.copy() if (marquees or viz_box or idle) else img
                for strip, pos in self.animated_strips(img, marquees, viz_box, idle):
                    shown.paste(strip, pos)
                self.sink.show(shown)
                if last is None:
                    log.info("first frame drawn")
                last = b
            # Wait for a tap, a state change or the next second; animate in the meantime.
            deadline = time.time() + (0.25 if (self.pressed is not None or self.toast) else 1.0)
            step = (1 / VIZ_FPS if viz_box else 1 / MARQUEE_FPS if marquees
                    else 1 / max(1, self.cfg["idle_fps"]) if idle else 0.1)
            while time.time() < deadline:
                try:
                    tap = self.taps.get(timeout=max(0.01, min(step, deadline - time.time())))
                    if len(tap) == 3:
                        self.handle_hold(tap[0], tap[1], screen)
                    else:
                        self.handle_tap(tap[0], tap[1], screen, data)
                    break
                except queue.Empty:
                    pass
                for strip, (x, y) in self.animated_strips(img, marquees, viz_box, idle):
                    self.sink.show_region(strip, x, y)
                if self.dirty.is_set():
                    self.dirty.clear()
                    break

    def animated_strips(self, base, marquees, viz_box, idle=None):
        """(strip, position) for everything that moves: scrolling lines, the spectrum and the idle clock's hands."""
        now = time.time()
        texts = {m.text for m in marquees}
        if texts != set(self.marquee_t0):
            # New text starts with the pause at the front again.
            self.marquee_t0 = {t: self.marquee_t0.get(t, now) for t in texts}
        strips = [(m.render(base, now - self.marquee_t0[m.text]), m.box[:2]) for m in marquees]
        if viz_box:
            self.spectrum.update(self.current_spectrum())
            strips.append((self.spectrum.render(base, viz_box, self.r.accent), viz_box[:2]))
        if idle:
            strips.append((idle.hands(datetime.datetime.now()), idle.face_box[:2]))
        return strips

    def current_spectrum(self):
        if self.demo:
            t = time.time()
            return [int(30000 * (0.55 + 0.45 * math.sin(t * 3 + i * 0.5)) * (1 - i / 40)) for i in range(VIZ_BINS)]
        return self.ss.current_spectrum()


__all__ = [
    "_blank", "same_track", "App",
]
