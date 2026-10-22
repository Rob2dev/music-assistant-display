"""Draws every screen."""
import datetime

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageStat

from .gfx import (
    ACCENT_FALLBACK, BG, BG_MAX_LUMA, ERROR, FG, FG_DIM, FG_FAINT, FG_SOFT, H, W, accent_from,
    default_background, ellipsize, fmt_time, font, icon, overflows, rounded, shade, wrap,
)
from .i18n import fmt_date, tr
from .layout import (
    GLASS_BAR, INFO_ROW_H, LIST_X0, LIST_X1, MAIN_BUTTONS, PICKER_H, PICKER_PITCH, PICKER_W, PICKER_X, POPUP,
    POPUP_BAR, POPUP_BUTTONS, POWER_PANEL, POWER_ROWS, ROOM_CONFIRM, ROOM_CONFIRM_BUTTONS, ROOM_PITCH, ROOM_ROWS,
    ROOM_ROW_H, ROOM_Y0, ROW_CY, SET_ROW_H, SET_ROW_PITCH, SET_STEP, SET_TAB_NAMES, SET_Y0, ST_COLS, ST_GAP,
    ST_HEADER, ST_ROWS, ST_TAB_NAMES, ST_TILE_H, ST_TILE_W, ST_X0, ST_Y0, SWITCH_H, SWITCH_MARGIN, SWITCH_W,
    tab_ranges,
)
from .music_assistant import MAClient
from .settings import LANGUAGE_CODES, LANG_NAMES, setting_text, settings_rows
from .strings import STRINGS
from .widgets import Marquee


class Renderer:
    def __init__(self):
        self.bg_key = None
        self.bg = None
        self.frost = None
        self.cover_img = None
        self._glass = {}
        self.marquees = []
        self.viz_box = None
        self.set_accent(ACCENT_FALLBACK)

    def set_accent(self, col):
        self.accent = col
        self.accent_dark = shade(col, 0.85)
        self.accent_light = shade(col, 1.45)

    def background(self, cover):
        if cover is self.bg_key and self.bg is not None:
            return self.bg
        self.bg_key = cover
        if cover is None:
            self.bg = default_background()
            self.cover_img = None
        else:
            blur = ImageOps.fit(cover, (W // 4, H // 4)).filter(ImageFilter.GaussianBlur(6)).resize((W, H))
            blur = ImageEnhance.Color(blur).enhance(1.35)
            self.bg = Image.blend(blur, Image.new("RGB", (W, H), (0, 0, 0)), 0.55)
            # Light covers (e.g. black-and-white photos) give a mid-grey background that swallows
            # dimmed text: cap the mean brightness.
            mean = ImageStat.Stat(self.bg.convert("L")).mean[0]
            if mean > BG_MAX_LUMA:
                self.bg = Image.blend(self.bg, Image.new("RGB", (W, H), (0, 0, 0)), 1 - BG_MAX_LUMA / mean)
            self.cover_img = ImageOps.fit(cover, (184, 184), Image.LANCZOS)
        self.set_accent(accent_from(cover))
        # Frosted glass: blurred even more; at quarter resolution, which is cheap on a Pi 2.
        self.frost = self.bg.resize((W // 4, H // 4)).filter(ImageFilter.GaussianBlur(3)).resize((W, H))
        self._glass = {}
        return self.bg

    def glass(self, img, box, radius=16, alpha=0.10, light=False):
        """Glass panel: frosted-glass crop + white tint + shine on top + light edge.

        Computed once per background and position, afterwards only pasted.
        """
        key = (box, radius, alpha, light)
        cached = self._glass.get(key)
        if cached is None:
            x0, y0, x1, y1 = box
            w, h = x1 - x0, y1 - y0
            white = Image.new("RGB", (w, h), (255, 255, 255))
            panel = Image.blend(self.frost.crop(box), white, alpha + (0.10 if light else 0))
            shine = Image.linear_gradient("L").resize((w, h)).point(lambda v: max(0, 40 - v // 3))
            panel = Image.composite(white, panel, shine).convert("RGBA")
            edge = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(edge).rounded_rectangle((0, 0, w - 1, h - 1), radius, outline=(255, 255, 255, 80), width=1)
            panel = Image.alpha_composite(panel, edge).convert("RGB")
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius, fill=255)
            cached = self._glass[key] = (panel, mask)
        img.paste(cached[0], box[:2], cached[1])

    def player(self, data, cover, error, pressed=None, toast=None, show_stations=False, show_viz=True,
               show_cover=True):
        a = data.get("attributes", {}) if data else {}
        state = data.get("state", "unavailable") if data else "unavailable"
        img = self.background(cover).copy()
        d = ImageDraw.Draw(img)

        # Cover or placeholder. With the cover switched off the text takes the whole width, in bigger type.
        wide = not show_cover
        cx0, cy0 = 16, 16
        if wide:
            pass
        elif self.cover_img is not None:
            c, m = rounded(self.cover_img, 12)
            img.paste(c, (cx0, cy0), m)
        else:
            self.glass(img, (cx0, cy0, cx0 + 184, cy0 + 184), radius=12, alpha=0.06)
            icon(d, "play", cx0 + 92, cy0 + 92, 30, (150, 150, 170))

        # Text
        tx, tw = (24, W - 48) if wide else (216, W - 216 - 16)
        t_sz, t_lh, a_sz, a_lh, al_sz, al_lh = (28, 34, 20, 26, 15, 20) if wide else (24, 30, 17, 22, 14, 19)
        duration = a.get("media_duration") or 0
        is_live = duration == 0 and bool(a.get("media_title")) and a.get("media_duration") is not None
        label = tr("status_" + state) if ("status_" + state) in STRINGS["en"] else state.upper()
        # The label always states the status (also for radio); the LIVE badge next to it states the kind of source.
        d.text((tx, 18), label, font=font(13, True), fill=self.accent if state == "playing" else FG_DIM)
        left_end = tx + d.textlength(label, font=font(13, True))
        if is_live:
            # LIVE badge right next to the label (red while playing)
            bx = int(tx + d.textlength(label, font=font(13, True)) + 8)
            live = tr("live")
            lw = d.textlength(live, font=font(11, True))
            d.rounded_rectangle((bx, 16, bx + lw + 20, 33), 8, fill=ERROR if state == "playing" else (90, 90, 100))
            d.ellipse((bx + 5, 21, bx + 11, 27), fill=FG)
            d.text((bx + 14, 18), live, font=font(11, True), fill=FG)
            left_end = bx + lw + 20
        # Time on the right of the label row: same small type, dimmed, so it stays secondary. A long status in
        # some languages (plus the LIVE badge) leaves no room for it: then it is left out.
        now_txt = datetime.datetime.now().strftime("%H:%M")
        time_w = d.textlength(now_txt, font=font(13, True))
        if left_end + 12 <= tx + tw - time_w:
            d.text((tx + tw - time_w, 18), now_txt, font=font(13, True), fill=FG_SOFT)

        # Title and artist: if they do not fit on 2 lines, use one scrolling line (see Marquee).
        # The scrolling text itself is not drawn here but by App.animated_strips.
        self.marquees = []
        title = a.get("media_title") or (
            tr("starting") if state == "starting"
            else tr("queue_empty") if state != "unavailable" else tr("player_unreachable"))
        album = a.get("media_album_name")
        if not a.get("media_title") and a.get("resume_name") and state != "unavailable":
            # Empty queue (e.g. after a player restart): play resumes the last played item.
            title, album = a["resume_name"], tr("resume_hint")
        y = 44 if wide else 40
        lines = wrap(d, title, font(t_sz, True), tw, 2)
        if overflows(lines, title):
            self.marquees.append(Marquee(title, font(t_sz, True), FG, (tx, y - 2, tx + tw, y + t_lh + 2)))
            y += t_lh
        else:
            for line in lines:
                d.text((tx, y), line, font=font(t_sz, True), fill=FG)
                y += t_lh
        y += 4
        artist = a.get("media_artist")
        if artist:
            lines = wrap(d, artist, font(a_sz), tw, 2)
            if overflows(lines, artist):
                self.marquees.append(Marquee(artist, font(a_sz), FG_DIM, (tx, y - 2, tx + tw, y + a_lh)))
                y += a_lh
            else:
                for line in lines:
                    d.text((tx, y), line, font=font(a_sz), fill=FG_DIM)
                    y += a_lh
        if album and album != title:
            for line in wrap(d, album, font(al_sz), tw, 2 if not artist else 1):
                d.text((tx, y + 2), line, font=font(al_sz), fill=FG_SOFT)
                y += al_lh

        # Free space between the text and the progress bar/LIVE badge: the spectrum goes here.
        vtop = max(y + 10, 112)
        # For radio (no progress bar) the spectrum runs down to the bottom of the cover (y=200).
        vbot = 164 if duration > 0 else cy0 + 184
        self.viz_box = (tx, vtop, tx + tw, vbot) if show_viz and vbot - vtop >= 24 and state == "playing" else None

        # Progress or LIVE badge
        py = 172
        if duration > 0:
            pos = a.get("media_position") or 0
            upd = a.get("media_position_updated_at")
            if state == "playing" and upd:
                try:
                    t = datetime.datetime.fromisoformat(upd)
                    pos += (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds()
                except ValueError:
                    pass
            pos = min(pos, duration)
            d.rounded_rectangle((tx, py, tx + tw, py + 4), 2, fill=(70, 70, 80))
            d.rounded_rectangle((tx, py, tx + max(4, int(tw * pos / duration)), py + 4), 2, fill=FG)
            d.text((tx, py + 10), fmt_time(pos), font=font(12), fill=FG_DIM)
            rt = fmt_time(duration)
            d.text((tx + tw - d.textlength(rt, font=font(12)), py + 10), rt, font=font(12), fill=FG_DIM)
        elif is_live:
            # The LIVE badge sits at the top next to the label; this row is free for the spectrum.
            if show_viz and self.viz_box is None and state == "playing":
                self.viz_box = (tx, py - 4, tx + tw, py + 22)

        # Buttons on a floating glass bar
        muted = a.get("is_volume_muted")
        playing = state == "playing"
        cy = ROW_CY
        self.glass(img, GLASS_BAR, radius=22, alpha=0.08)
        for b, (x0, x1) in MAIN_BUTTONS.items():
            if b == "stations" and not show_stations:
                continue
            cx = (x0 + x1) // 2
            if pressed == b and b != "play":
                bx0, bx1 = max(x0, GLASS_BAR[0]) + 8, min(x1, GLASS_BAR[2]) - 8
                self.glass(img, (bx0, GLASS_BAR[1] + 8, bx1, GLASS_BAR[3] - 8), radius=14, alpha=0.08, light=True)
            if b == "play":
                # Glass circle with a white icon: stands out by size, not by colour.
                self.glass(img, (cx - 32, cy - 32, cx + 32, cy + 32), radius=32, alpha=0.2, light=pressed == b)
                icon(d, "pause" if playing else "play", cx + (0 if playing else 3), cy, 15, FG)
            elif b == "volume":
                # Deliberately small and dimmed; accent colour while muted.
                icon(d, "muted" if muted else "mute", cx, cy, 11, self.accent if muted else FG_SOFT)
            elif b == "stations":
                icon(d, "radio", cx, cy + 2, 11, FG_SOFT)
            else:
                icon(d, b, cx, cy, 14, FG)

        self._overlay(d, error, toast)
        return img

    def stations(self, snap, tab, page, current, pressed=None, toast=None, cover=None):
        """Station picker: tabs, 3x2 glass tiles with logo and name."""
        img = self.background(cover).copy()
        d = ImageDraw.Draw(img)
        for name, (x0, x1) in tab_ranges(ST_TAB_NAMES).items():
            label = tr("tab_" + name)
            active = name == tab
            f = font(16, True)
            tx = x0 + 12
            d.text((tx, 16), label, font=f, fill=FG if active else FG_FAINT)
            if active:
                d.rounded_rectangle((tx, 40, tx + d.textlength(label, font=f), 43), 2, fill=self.accent)
        rooms = tab == "rooms"
        items = snap["rooms"]["rows"] if rooms else snap["stations"].get(tab) or []
        per = ROOM_ROWS if rooms else ST_COLS * ST_ROWS
        pages = max(1, -(-len(items) // per))
        page = min(page, pages - 1)
        if pages > 1:
            (lx0, lx1), (rx0, rx1) = ST_HEADER["left"], ST_HEADER["right"]
            icon(d, "left", (lx0 + lx1) // 2, 26, 8, FG if page > 0 else (60, 60, 70))
            icon(d, "right", (rx0 + rx1) // 2, 26, 8, FG if page < pages - 1 else (60, 60, 70))
            pt = f"{page + 1}/{pages}"
            d.text(((lx1 + rx0) / 2 - d.textlength(pt, font=font(12)) / 2, 19), pt, font=font(12), fill=FG_SOFT)
        cx0, cx1 = ST_HEADER["close"]
        icon(d, "close", (cx0 + cx1) // 2, 26, 8, FG_DIM)

        if not items:
            if rooms:
                error = snap["rooms_error"]
                msg = tr("rooms_failed") if error else (
                    tr("rooms_loading") if snap["rooms_loading"] or snap["rooms"]["leader"] is None
                    else tr("rooms_none"))
            else:
                error = snap["error"]
                msg = tr("stations_failed") if error else (
                    tr("stations_loading") if snap["loading"] or not snap["loaded"] else tr("stations_none"))
            d.text(((W - d.textlength(msg, font=font(16))) / 2, 140), msg, font=font(16),
                   fill=ERROR if error else FG_DIM)
            if error:
                # Tapping anywhere in the empty area retries (see App.stations_tap).
                hint = tr("tap_retry")
                hw = d.textlength(hint, font=font(14, True))
                bx0, by0 = int((W - hw) / 2 - 18), 176
                self.glass(img, (bx0, by0, int(bx0 + hw + 36), by0 + 40), radius=20, alpha=0.1)
                d.text((bx0 + 18, by0 + 10), hint, font=font(14, True), fill=FG)
        if rooms:
            self._draw_rooms(img, d, snap, items, page, pressed)
            self._overlay(d, None, toast)
            return img, page
        cur = (current or "").casefold()
        for i, st in enumerate(items[page * per:(page + 1) * per]):
            r, c = divmod(i, ST_COLS)
            x = ST_X0 + c * (ST_TILE_W + ST_GAP)
            y = ST_Y0 + r * (ST_TILE_H + ST_GAP)
            playing_here = cur and st["name"].casefold() == cur
            self.glass(img, (x, y, x + ST_TILE_W, y + ST_TILE_H), radius=14, alpha=0.08,
                       light=pressed == page * per + i)
            if playing_here:
                d.rounded_rectangle((x, y, x + ST_TILE_W - 1, y + ST_TILE_H - 1), 14, outline=self.accent, width=2)
            lx, ly = x + (ST_TILE_W - MAClient.LOGO) // 2, y + 10
            if st["logo"] is not None:
                logo, m = rounded(st["logo"], 10)
                img.paste(logo, (lx, ly), m)
            else:
                d.rounded_rectangle((lx, ly, lx + MAClient.LOGO, ly + MAClient.LOGO), 10, fill=(45, 45, 54))
                icon(d, "radio", lx + MAClient.LOGO // 2, ly + MAClient.LOGO // 2 + 4, 18, FG_FAINT)
            name = ellipsize(d, st["name"], font(13, True), ST_TILE_W - 14)
            d.text((x + (ST_TILE_W - d.textlength(name, font=font(13, True))) / 2, y + 92), name,
                   font=font(13, True), fill=self.accent if playing_here else FG)
        self._overlay(d, None, toast)
        return img, page

    def _draw_rooms(self, img, d, snap, items, page, pressed):
        """This player on top, then the players that are (or could be) in its group, each with a switch."""
        leader = snap["rooms"]["leader"]
        if leader:
            d.ellipse((24, 70, 34, 80), fill=self.accent)
            name = ellipsize(d, leader["name"], font(15, True), 260)
            d.text((44, 64), name, font=font(15, True), fill=FG)
            d.text((44 + d.textlength(name, font=font(15, True)) + 10, 66), tr("rooms_this"), font=font(13),
                   fill=FG_FAINT)
        for i, row in enumerate(items[page * ROOM_ROWS:(page + 1) * ROOM_ROWS]):
            y = ROOM_Y0 + i * ROOM_PITCH
            self.glass(img, (LIST_X0, y, LIST_X1, y + ROOM_ROW_H), radius=12, alpha=0.08, light=pressed == page * ROOM_ROWS + i)
            off = not row["available"]
            d.text((34, y + 11), ellipsize(d, row["name"], font(16, True), 270), font=font(16, True),
                   fill=FG_FAINT if off else FG)
            sx, sy = LIST_X1 - SWITCH_MARGIN - SWITCH_W, y + 9
            if off:
                label = tr("rooms_offline")
                d.text((sx - 14 - d.textlength(label, font=font(13)), y + 14), label, font=font(13), fill=FG_FAINT)
            on = row["member"]
            d.rounded_rectangle((sx, sy, sx + SWITCH_W, sy + SWITCH_H), SWITCH_H // 2,
                                fill=(45, 45, 54) if off else (self.accent if on else (70, 70, 82)))
            kx = sx + SWITCH_W - 14 if on else sx + 14
            d.ellipse((kx - 10, sy + 3, kx + 10, sy + 23), fill=(110, 110, 120) if off else FG)

    def room_confirm(self, img, name, add, pressed=None):
        """Confirmation over the rooms list: add/remove a player."""
        img = Image.blend(img, Image.new("RGB", (W, H), (0, 0, 0)), 0.35)
        d = ImageDraw.Draw(img)
        x0, y0, x1, y1 = ROOM_CONFIRM
        self.glass(img, ROOM_CONFIRM, radius=20, alpha=0.14)
        y = y0 + 26
        for line in wrap(d, tr("room_add_q" if add else "room_remove_q", name=name), font(19, True), x1 - x0 - 48, 3):
            d.text((x0 + 24, y), line, font=font(19, True), fill=FG)
            y += 26
        for key, box in ROOM_CONFIRM_BUTTONS.items():
            if key == "yes":
                d.rounded_rectangle(box, 14, fill=self.accent_dark if pressed == key else self.accent)
                label, col = tr("room_add" if add else "room_remove"), (20, 20, 24)
            else:
                self.glass(img, box, radius=14, alpha=0.18, light=pressed == key)
                label, col = tr("power_cancel"), FG
            d.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), label, font=font(17, True), fill=col, anchor="mm")
        return img

    def volume_popup(self, img, data, pressed=None):
        """Volume panel on top of the player screen."""
        a = data.get("attributes", {}) if data else {}
        vol = a.get("volume_level") or 0
        muted = bool(a.get("is_volume_muted"))
        img = Image.blend(img, Image.new("RGB", (W, H), (0, 0, 0)), 0.35)
        d = ImageDraw.Draw(img)
        x0, y0, x1, y1 = POPUP
        self.glass(img, POPUP, radius=20, alpha=0.14)
        d.text((x0 + 24, y0 + 22), tr("volume"), font=font(13, True), fill=FG_DIM)
        txt = tr("muted") if muted else f"{round(vol * 100)}%"
        f = font(30, True)
        d.text((x1 - 24 - d.textlength(txt, font=f), y0 + 10), txt, font=f, fill=FG_FAINT if muted else FG)
        bx0, bx1, by = POPUP_BAR
        d.rounded_rectangle((bx0, by - 6, bx1, by + 6), 6, fill=(60, 60, 70))
        kx = bx0 + int((bx1 - bx0) * vol)
        if not muted:
            d.rounded_rectangle((bx0, by - 6, max(bx0 + 12, kx), by + 6), 6, fill=self.accent)
        # Knob on the bar: shows that you can tap it
        d.ellipse((kx - 11, by - 11, kx + 11, by + 11), fill=FG_FAINT if muted else FG)
        for name, (px0, py0, px1, py1) in POPUP_BUTTONS.items():
            active = name == "mute" and muted
            if active:
                d.rounded_rectangle((px0, py0, px1, py1), 14, fill=self.accent_dark if pressed == name else self.accent)
            else:
                self.glass(img, (px0, py0, px1, py1), radius=14, alpha=0.18, light=pressed == name)
            kind = {"vol-": "vol-", "vol+": "vol+", "mute": "muted" if muted else "mute"}[name]
            icon(d, kind, (px0 + px1) // 2, (py0 + py1) // 2, 18, (20, 20, 24) if active else FG)
        return img

    def power_menu(self, img, pressed=None):
        """Power menu on top of the player screen: shut down, restart or cancel."""
        img = Image.blend(img, Image.new("RGB", (W, H), (0, 0, 0)), 0.35)
        d = ImageDraw.Draw(img)
        x0, y0, x1, y1 = POWER_PANEL
        self.glass(img, POWER_PANEL, radius=20, alpha=0.14)
        d.text((x0 + 24, y0 + 14), tr("menu_title"), font=font(13, True), fill=FG_DIM)
        for name, (bx0, by0, bx1, by1) in POWER_ROWS.items():
            self.glass(img, (bx0, by0, bx1, by1), radius=14, alpha=0.18, light=pressed == name)
            col = ERROR if name == "shutdown" else FG
            kind = {"settings": "gear", "shutdown": "power", "restart": "restart", "cancel": "close"}[name]
            icon(d, kind, bx0 + 34, (by0 + by1) // 2, 13, col)
            label = tr("menu_settings") if name == "settings" else tr("power_" + name)
            d.text((bx0 + 64, (by0 + by1) / 2 - 12), label, font=font(18, True), fill=col)
        return img

    def settings_screen(self, cfg, tab, pressed, info, toast=None):
        """Settings: tabs (Settings / Info), then the setting rows or the system info."""
        img = self.background(None).copy()
        d = ImageDraw.Draw(img)
        for name, (x0, x1) in tab_ranges(SET_TAB_NAMES).items():
            label, f = tr("tab_" + name), font(16, True)
            d.text((x0 + 12, 16), label, font=f, fill=FG if name == tab else FG_FAINT)
            if name == tab:
                d.rounded_rectangle((x0 + 12, 40, x0 + 12 + d.textlength(label, font=f), 43), 2, fill=self.accent)
        cx0, cx1 = ST_HEADER["close"]
        icon(d, "close", (cx0 + cx1) // 2, 26, 8, FG_DIM)
        if tab == "info":
            self.glass(img, (LIST_X0, SET_Y0, LIST_X1, SET_Y0 + len(info) * INFO_ROW_H), radius=14, alpha=0.08)
            for i, (key, value) in enumerate(info):
                y = SET_Y0 + i * INFO_ROW_H
                d.text((34, y + 13), tr(key), font=font(14), fill=FG_DIM)
                text = ellipsize(d, value, font(15, True), 250)
                d.text((446 - d.textlength(text, font=font(15, True)), y + 12), text, font=font(15, True), fill=FG)
        else:
            for i, sd in enumerate(settings_rows(tab)):
                y = SET_Y0 + i * SET_ROW_PITCH
                self.glass(img, (LIST_X0, y, LIST_X1, y + SET_ROW_H), radius=12, alpha=0.08, light=pressed == i)
                action = sd["kind"] == "action"
                d.text((34, y + 9), ellipsize(d, tr(sd["label"]), font(16, True), 410 if action else 250),
                       font=font(16, True), fill=FG_DIM if action else FG)
                if sd["kind"] == "cycle":
                    text = setting_text(sd, cfg[sd["key"]])
                    d.text((444 - d.textlength(text, font=font(16, True)), y + 9), text, font=font(16, True),
                           fill=self.accent)
                elif sd["kind"] == "toggle":
                    on = cfg[sd["key"]]
                    sx, sy = LIST_X1 - SWITCH_MARGIN - SWITCH_W, y + (SET_ROW_H - SWITCH_H) // 2
                    d.rounded_rectangle((sx, sy, sx + SWITCH_W, sy + SWITCH_H), SWITCH_H // 2,
                                        fill=self.accent if on else (70, 70, 82))
                    kx = sx + SWITCH_W - 14 if on else sx + 14
                    d.ellipse((kx - 10, sy + 3, kx + 10, sy + 23), fill=FG)
                elif sd["kind"] == "step":
                    for symbol, name in (("−", "minus"), ("+", "plus")):
                        bx, bx1 = SET_STEP[name]
                        self.glass(img, (bx, y + 5, bx1, y + SET_ROW_H - 5), radius=10, alpha=0.16)
                        d.text(((bx + bx1) // 2, y + SET_ROW_H // 2), symbol, font=font(20, True), fill=FG, anchor="mm")
                    d.text((372, y + SET_ROW_H // 2), setting_text(sd, cfg[sd["key"]]), font=font(15, True),
                           fill=self.accent, anchor="mm")
        self._overlay(d, None, toast)
        return img

    def language_picker(self, active, pressed=None):
        """The settings screen's language list: every language by its own name, the active one marked."""
        img = self.background(None).copy()
        d = ImageDraw.Draw(img)
        label, f = tr("set_language"), font(16, True)
        d.text((24, 16), label, font=f, fill=FG)
        d.rounded_rectangle((24, 40, 24 + d.textlength(label, font=f), 43), 2, fill=self.accent)
        cx0, cx1 = ST_HEADER["close"]
        icon(d, "close", (cx0 + cx1) // 2, 26, 8, FG_DIM)
        for i, code in enumerate(LANGUAGE_CODES):
            x, y = PICKER_X[i % 2], SET_Y0 + (i // 2) * PICKER_PITCH
            on = code == active
            self.glass(img, (x, y, x + PICKER_W, y + PICKER_H), radius=12, alpha=0.12 if on else 0.08, light=pressed == i)
            if on:
                d.rounded_rectangle((x, y, x + PICKER_W - 1, y + PICKER_H - 1), 12, outline=self.accent, width=2)
            d.text((x + 18, y + 11), LANG_NAMES[code], font=font(17, True), fill=self.accent if on else FG)
        return img

    def clock(self, data, error, toast=None):
        img = Image.new("RGB", (W, H), (0, 0, 0))
        d = ImageDraw.Draw(img)
        now = datetime.datetime.now()
        t = now.strftime("%H:%M")
        f = font(112, True)
        d.text(((W - d.textlength(t, font=f)) / 2, 58), t, font=f, fill=(200, 200, 208))
        ds = fmt_date(now)
        d.text(((W - d.textlength(ds, font=font(22))) / 2, 196), ds, font=font(22), fill=FG_FAINT)
        a = data.get("attributes", {}) if data else {}
        if a.get("media_title"):
            s = ellipsize(d, tr("clock_last") + a["media_title"], font(15), W - 60)
            d.text(((W - d.textlength(s, font=font(15))) / 2, 262), s, font=font(15), fill=(80, 80, 90))
        hint = tr("clock_hint")
        d.text(((W - d.textlength(hint, font=font(12))) / 2, 290), hint, font=font(12), fill=(60, 60, 68))
        self._overlay(d, error, toast)
        return img

    def message(self, title, lines):
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        d.text((24, 28), title, font=font(24, True), fill=self.accent)
        y = 76
        for line in lines:
            for part in wrap(d, line, font(16), W - 48, 3):
                d.text((24, y), part, font=font(16), fill=FG_DIM)
                y += 22
            y += 8
        return img

    def _overlay(self, d, error, toast):
        if error:
            tw = d.textlength(error, font=font(12, True))
            d.rounded_rectangle((W - tw - 34, 6, W - 6, 26), 10, fill=ERROR)
            d.text((W - tw - 20, 9), error, font=font(12, True), fill=FG)
        if toast:
            tw = d.textlength(toast, font=font(15, True))
            x0 = (W - tw) / 2 - 16
            d.rounded_rectangle((x0, 128, x0 + tw + 32, 162), 17, fill=(30, 30, 36), outline=self.accent, width=2)
            d.text((x0 + 16, 136), toast, font=font(15, True), fill=FG)


__all__ = [
    "Renderer",
]
