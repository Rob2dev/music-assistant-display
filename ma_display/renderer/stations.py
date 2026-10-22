"""The station picker and the rooms list, with the add/remove confirmation."""

from PIL import Image, ImageDraw

from ..gfx import ERROR, FG, FG_DIM, FG_FAINT, FG_SOFT, H, W, ellipsize, font, icon, rounded, wrap
from ..i18n import tr
from ..layout import (
    LIST_X0, LIST_X1, ROOM_CONFIRM, ROOM_CONFIRM_BUTTONS, ROOM_PITCH, ROOM_ROWS, ROOM_ROW_H, ROOM_Y0,
    ST_COLS, ST_GAP, ST_HEADER, ST_ROWS, ST_TAB_NAMES, ST_TILE_H, ST_TILE_W, ST_X0, ST_Y0, SWITCH_H,
    SWITCH_MARGIN, SWITCH_W, tab_ranges,
)
from ..music_assistant import MAClient


class StationScreens:
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


__all__ = ["StationScreens"]
