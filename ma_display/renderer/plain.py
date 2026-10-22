"""The big clock and the plain message screen."""
import datetime

from PIL import Image, ImageDraw

from ..gfx import BG, FG_DIM, FG_FAINT, H, W, ellipsize, font, wrap
from ..i18n import fmt_date, tr


class PlainScreens:
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


__all__ = ["PlainScreens"]
