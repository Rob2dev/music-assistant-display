"""Moving parts that draw themselves: scrolling text, the spectrum and the idle clock."""
import math
import time

from PIL import Image, ImageChops, ImageDraw, ImageOps

from .gfx import (
    ACCENT_FALLBACK, FG, FG_FAINT, FG_SOFT, H, W, accent_from, ellipsize, font, icon, rounded, shade, wrap,
)
from .i18n import current_language, fmt_date, tr


MARQUEE_FPS = 15


class Marquee:
    """One line of text that scrolls horizontally when it is too long.

    The box is updated separately from the rest of the picture: first PAUSE seconds still
    at the start, then SPEED px/s to the left, with a second copy GAP px behind so the loop
    is seamless. The edges fade out softly.
    """

    PAUSE, SPEED, GAP, FADE = 2.0, 40, 56, 14
    _masks = {}

    def __init__(self, text, fnt, color, box, text_dy=2):
        self.text, self.font, self.color, self.box, self.text_dy = text, fnt, color, box, text_dy
        self.text_w = ImageDraw.Draw(Image.new("L", (1, 1))).textlength(text, font=fnt)
        self._alpha = None  # text (twice, with a gap) as a mask, rendered once
        self._bg_src = self._bg = self._fg = None

    def offset(self, t):
        period = self.PAUSE + (self.text_w + self.GAP) / self.SPEED
        phase = t % period
        return 0.0 if phase < self.PAUSE else (phase - self.PAUSE) * self.SPEED

    def _mask(self, w, h, left):
        key = (w, h, left)
        if key not in self._masks:
            ramp = [min(255, int(255 * min(x, w - 1 - x) / self.FADE)) for x in range(w)]
            if not left:  # at rest the text sits at the start: do not fade the left edge
                ramp = [255 if x < w // 2 else v for x, v in enumerate(ramp)]
            line = Image.new("L", (w, 1))
            line.putdata(ramp)
            self._masks[key] = line.resize((w, h))
        return self._masks[key]

    def render(self, base, t):
        """The strip for this box at time t (seconds since the text appeared).

        Rendering text is expensive on a Pi 2, so it is drawn once into a mask and each step
        only slides the window over it (all C operations inside Pillow).
        """
        x0, y0, x1, y1 = self.box
        w, h = x1 - x0, y1 - y0
        if self._bg_src is not base:
            self._bg_src, self._bg = base, base.crop(self.box)
            self._fg = Image.new("RGB", (w, h), self.color)
        if self._alpha is None:
            step = int(self.text_w + self.GAP)
            self._alpha = Image.new("L", (step + w + 2, h), 0)
            d = ImageDraw.Draw(self._alpha)
            for x in (0, step):
                d.text((x, self.text_dy), self.text, font=self.font, fill=255)
        off = int(self.offset(t))
        alpha = ImageChops.multiply(self._alpha.crop((off, 0, off + w, h)), self._mask(w, h, off > 0))
        return Image.composite(self._fg, self._bg, alpha)


class SpectrumViz:
    """Spectrum bars: fast up, slow back down (like a VU meter)."""

    DECAY = 1.6     # level drop per second
    # MA delivers 0-65535; for music the mel bands mostly sit between ~15000 and ~40000.
    # Stretching that range to 0-1 shows visible rhythm instead of a flat row.
    FLOOR, FULL = 12000, 40000

    def __init__(self, bins):
        self.levels = [0.0] * bins
        self.t = time.time()

    def update(self, spectrum):
        now = time.time()
        dt, self.t = min(0.2, now - self.t), now
        for i, v in enumerate(spectrum or [0] * len(self.levels)):
            if i >= len(self.levels):
                break
            target = min(1.0, max(0.0, (v - self.FLOOR) / (self.FULL - self.FLOOR)))
            self.levels[i] = target if target > self.levels[i] else max(target, self.levels[i] - self.DECAY * dt)

    @property
    def idle(self):
        return max(self.levels) < 0.01

    def render(self, base, box, color):
        x0, y0, x1, y1 = box
        strip = base.crop(box)
        d = ImageDraw.Draw(strip)
        w, h, n = x1 - x0, y1 - y0, len(self.levels)
        gap = 2
        bw = (w - gap * (n - 1)) / n
        top = shade(color, 1.35)
        for i, lvl in enumerate(self.levels):
            bh = max(2, int(lvl * h))
            bx = int(i * (bw + gap))
            d.rounded_rectangle((bx, h - bh, int(bx + bw) - 1, h - 1), 2, fill=color)
            if bh > 6:  # light cap: looks like glass
                d.rectangle((bx, h - bh, int(bx + bw) - 1, h - bh + 1), fill=top)
        return strip


class IdleClock:
    """The idle screen: an analog clock with the date and a tile for the last station.

    The face, date and tile are drawn once and cached (they only change with the date, the last station
    or the drift offset); each frame only draws the hands on a copy of the face area, so a frame costs a
    few milliseconds. The whole picture drifts slowly (about a pixel every few seconds), so no static
    image sits on the panel. After the night threshold it becomes a dim digital time that drifts further.
    """

    CENTER, R = (168, 160), 112
    SS = 2  # supersampling of the face and the hands (anti-aliasing)
    TEXT = (205, 205, 214)

    def __init__(self):
        self.key = None
        self.base = None       # the full picture without hands
        self.face = None       # the face area of `base`
        self.face_box = None   # where the face area sits on screen
        self.tile_box = None   # where the "last station" tile sits (tappable), or None
        self.animated = False  # does it need the hands drawn each frame?
        self.accent = ACCENT_FALLBACK
        self._gradients = {}

    @staticmethod
    def offset(t, scale=1):
        """Slow Lissajous drift of the whole picture, in pixels."""
        return (round(14 * scale * math.sin(t / 600 * 2 * math.pi)),
                round(8 * scale * math.sin(t / 420 * 2 * math.pi)))

    @staticmethod
    def _mix(a, b, t):
        return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

    def _gradient(self, top, bottom):
        if (top, bottom) not in self._gradients:
            g = Image.linear_gradient("L").resize((W, H))
            self._gradients[(top, bottom)] = Image.composite(
                Image.new("RGB", (W, H), bottom), Image.new("RGB", (W, H), top), g)
        return self._gradients[(top, bottom)]

    def render(self, now, last, logo, night, t=None):
        """The static picture for this moment; the very same object while nothing changed."""
        t = time.time() if t is None else t
        off = self.offset(t, 4 if night else 1)
        if night:
            key = ("night", off, now.hour, now.minute, current_language())
        else:
            key = ("day", off, now.date(), last["uri"] if last else None, id(logo), current_language())
        if key == self.key:
            return self.base
        self.key = key
        if night:
            img = self._gradient((4, 5, 9), (9, 7, 16)).copy()
            d = ImageDraw.Draw(img)
            cx, cy = 240 + off[0], 166 + off[1]
            d.text((cx, cy), now.strftime("%H:%M"), font=font(72, True), fill=(62, 62, 72), anchor="mm")
            d.text((cx, cy + 50), fmt_date(now), font=font(16), fill=(46, 46, 56), anchor="mm")
            self.base, self.animated, self.face_box, self.face, self.tile_box = img, False, None, None, None
            return img

        img = self._gradient((8, 10, 18), (20, 14, 34)).copy()
        self.accent = accent_from(logo) if logo is not None else ACCENT_FALLBACK
        # the colour of the text drifts with the time of day (0 .. 0.5 towards the accent colour)
        tint = 0.25 + 0.25 * math.sin((now.hour + now.minute / 60 - 6) / 24 * 2 * math.pi)
        text = self._mix(self.TEXT, self.accent, tint)
        dx, dy = off
        cx, cy = self.CENTER[0] + dx, self.CENTER[1] + dy
        S, Rr = self.SS, self.R
        size = (Rr + 8) * 2
        layer = Image.new("RGBA", (size * S, size * S), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        c = size * S // 2
        ld.ellipse((c - Rr * S, c - Rr * S, c + Rr * S, c + Rr * S), outline=(70, 70, 88, 255), width=3 * S)
        for i in range(60):
            a = math.radians(i * 6 - 90)
            major = i % 5 == 0
            r0, r1 = Rr * S - (14 if major else 6) * S, Rr * S - 4 * S
            ld.line((c + r0 * math.cos(a), c + r0 * math.sin(a), c + r1 * math.cos(a), c + r1 * math.sin(a)),
                    fill=(150, 150, 164, 255) if major else (80, 80, 96, 255), width=(3 if major else 1) * S)
        layer = layer.reduce(S)
        fx, fy = cx - size // 2, cy - size // 2
        img.paste(layer, (fx, fy), layer)

        d = ImageDraw.Draw(img)
        x0 = 300 + dx
        yw = (84 if last else 118) + dy
        weekday = tr("weekdays")[now.weekday()]
        date = tr("date_short", day=now.day, month=tr("months")[now.month - 1])
        wd_font = next((font(s, True) for s in (20, 19, 18, 17) if d.textlength(weekday, font=font(s, True)) <= 156),
                       font(17, True))
        d.text((x0, yw), ellipsize(d, weekday, wd_font, 156), font=wd_font, fill=text)
        d.text((x0, yw + 30), ellipsize(d, date, font(17), 156), font=font(17),
               fill=self._mix((120, 120, 132), self.accent, tint * 0.6))
        self.tile_box = None
        if last:
            box = (x0 - 4, 158 + dy, x0 + 154, 252 + dy)
            w, h = box[2] - box[0], box[3] - box[1]
            region = img.crop(box).convert("RGBA")
            ov = Image.new("RGBA", region.size, (0, 0, 0, 0))
            ImageDraw.Draw(ov).rounded_rectangle((0, 0, w - 1, h - 1), 14, fill=(255, 255, 255, 24),
                                                 outline=(255, 255, 255, 56))
            img.paste(Image.alpha_composite(region, ov).convert("RGB"), box[:2])
            d = ImageDraw.Draw(img)
            if logo is not None:
                lg, mask = rounded(ImageOps.fit(logo, (44, 44), Image.LANCZOS), 9)
                img.paste(lg, (box[0] + 8, box[1] + 14), mask)
            else:
                d.rounded_rectangle((box[0] + 8, box[1] + 14, box[0] + 52, box[1] + 58), 9, fill=(45, 45, 54))
                icon(d, "radio", box[0] + 30, box[1] + 38, 10, FG_FAINT)
            tx, tw = box[0] + 60, w - 60 - 6
            y = box[1] + 12
            for line in wrap(d, last["name"], font(14, True), tw, 2):
                d.text((tx, y), line, font=font(14, True), fill=FG)
                y += 17
            for line in wrap(d, tr("tap_resume"), font(12), tw, 2):
                d.text((tx, y + 3), line, font=font(12), fill=self._mix(FG_SOFT, self.accent, 0.5))
                y += 15
            self.tile_box = box
        self.base, self.animated = img, True
        self.face_box = (fx, fy, fx + size, fy + size)
        self.face = img.crop(self.face_box)
        return img

    def hands(self, now):
        """The face area with the hands for this moment (None when there is no face)."""
        if not self.animated:
            return None
        x0, _, x1, _ = self.face_box
        size, S = x1 - x0, self.SS
        layer = Image.new("RGBA", (size * S, size * S), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        c, Rr = size * S // 2, self.R * S
        ink, acc = self.TEXT + (255,), self.accent + (255,)

        def hand(angle, length, width, col, tail=0):
            a = math.radians(angle - 90)
            d.line((c - tail * math.cos(a), c - tail * math.sin(a), c + length * math.cos(a), c + length * math.sin(a)),
                   fill=col, width=width)

        sec = now.second + now.microsecond / 1e6
        minute = now.minute + sec / 60
        hour = now.hour % 12 + minute / 60
        hand(hour * 30, Rr * 0.52, 7 * S, ink)
        hand(minute * 6, Rr * 0.80, 5 * S, ink)
        hand(sec * 6, Rr * 0.88, 2 * S, acc, tail=22 * S)
        d.ellipse((c - 7 * S, c - 7 * S, c + 7 * S, c + 7 * S), fill=acc)
        layer = layer.reduce(S)
        out = self.face.copy()
        out.paste(layer, (0, 0), layer)
        return out


__all__ = [
    "MARQUEE_FPS", "Marquee", "SpectrumViz", "IdleClock",
]
