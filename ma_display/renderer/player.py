"""The now-playing screen."""
import datetime

from PIL import ImageDraw

from ..gfx import ERROR, FG, FG_DIM, FG_SOFT, W, fmt_time, font, icon, overflows, rounded, wrap
from ..i18n import tr
from ..layout import GLASS_BAR, MAIN_BUTTONS, ROW_CY
from ..strings import STRINGS
from ..widgets import Marquee


class PlayerScreen:
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


__all__ = ["PlayerScreen"]
