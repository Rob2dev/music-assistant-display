"""Colours, fonts and the small drawing helpers every screen uses."""
import math

from PIL import Image, ImageChops, ImageDraw, ImageFont


W, H = 480, 320


FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


# Colours
BG = (14, 14, 18)


FG = (240, 240, 244)


FG_DIM = (170, 170, 180)


FG_FAINT = (110, 110, 120)


FG_SOFT = (150, 150, 160)  # secondary text on a (sometimes light) background: time, album line


BG_MAX_LUMA = 55  # maximum mean brightness of the blurred cover background


ACCENT = (255, 138, 61)  # only used by the calibration screen now


ACCENT_FALLBACK = (79, 195, 247)  # ice blue, for covers without a usable colour


ERROR = (235, 87, 87)


_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(FONT_BOLD if bold else FONT, size)
    return _fonts[key]


# ---------------------------------------------------------------- drawing

def ellipsize(draw, text, fnt, maxw):
    if draw.textlength(text, font=fnt) <= maxw:
        return text
    while text and draw.textlength(text + "…", font=fnt) > maxw:
        text = text[:-1]
    return text.rstrip() + "…"


def wrap(draw, text, fnt, maxw, max_lines):
    words, lines, cur = text.split(), [], ""
    for i, w in enumerate(words):
        test = (cur + " " + w).strip()
        if not cur or draw.textlength(test, font=fnt) <= maxw:
            cur = test
            continue
        if len(lines) == max_lines - 1:
            # Last line: append the rest so ellipsize adds the "…".
            cur = " ".join([cur] + words[i:])
            break
        lines.append(cur)
        cur = w
    if cur:
        lines.append(cur)
    return [ellipsize(draw, line, fnt, maxw) for line in lines]


def overflows(lines, text):
    """Did wrap() have to truncate the text?"""
    return bool(lines) and lines[-1].endswith("…") and not text.rstrip().endswith("…")


def fmt_time(sec):
    sec = max(0, int(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def rounded(img, radius):
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.size[0] - 1, img.size[1] - 1), radius, fill=255)
    return img, mask


def icon(d, kind, cx, cy, s, col):
    """Simple vector icons, s = half size."""
    if kind == "play":
        d.polygon([(cx - s * 0.55, cy - s), (cx - s * 0.55, cy + s), (cx + s, cy)], fill=col)
    elif kind == "pause":
        w = s * 0.38
        d.rectangle((cx - s * 0.75, cy - s, cx - s * 0.75 + w, cy + s), fill=col)
        d.rectangle((cx + s * 0.75 - w, cy - s, cx + s * 0.75, cy + s), fill=col)
    elif kind in ("prev", "next"):
        m = -1 if kind == "prev" else 1
        d.polygon([(cx - m * s * 0.8, cy - s * 0.8), (cx - m * s * 0.8, cy + s * 0.8), (cx + m * s * 0.45, cy)], fill=col)
        x = cx + m * s * 0.55
        d.rectangle((min(x, x + m * s * 0.25), cy - s * 0.8, max(x, x + m * s * 0.25), cy + s * 0.8), fill=col)
    elif kind == "radio":
        t = max(2, int(s * 0.16))
        d.line((cx - s * 0.55, cy - s * 0.5, cx + s * 0.45, cy - s * 1.05), fill=col, width=t)  # antenna
        d.rounded_rectangle((cx - s, cy - s * 0.5, cx + s, cy + s * 0.8), int(s * 0.25), outline=col, width=t)
        d.ellipse((cx - s * 0.72, cy - s * 0.22, cx - s * 0.08, cy + s * 0.5), outline=col, width=t)  # speaker
        for dy in (-0.12, 0.25):
            d.line((cx + s * 0.18, cy + s * dy, cx + s * 0.7, cy + s * dy), fill=col, width=t)
    elif kind == "gear":
        t = max(2, int(s * 0.22))
        for i in range(8):
            a = math.radians(i * 45)
            d.line((cx + s * 0.62 * math.cos(a), cy + s * 0.62 * math.sin(a), cx + s * math.cos(a), cy + s * math.sin(a)),
                   fill=col, width=t + 1)
        d.ellipse((cx - s * 0.7, cy - s * 0.7, cx + s * 0.7, cy + s * 0.7), outline=col, width=t)
        d.ellipse((cx - s * 0.25, cy - s * 0.25, cx + s * 0.25, cy + s * 0.25), fill=col)
    elif kind == "power":
        t = max(2, int(s * 0.18))
        d.arc((cx - s, cy - s, cx + s, cy + s), -55, 235, fill=col, width=t)
        d.line((cx, cy - s * 1.1, cx, cy - s * 0.1), fill=col, width=t)
    elif kind == "restart":
        t = max(2, int(s * 0.18))
        d.arc((cx - s, cy - s, cx + s, cy + s), 20, 320, fill=col, width=t)
        a = math.radians(320)  # arrow head at the end of the arc, pointing along the clockwise tangent
        px, py = cx + s * math.cos(a), cy + s * math.sin(a)
        tx, ty, nx, ny, k = -math.sin(a), math.cos(a), math.cos(a), math.sin(a), s * 0.6
        d.polygon([(px + tx * k * 1.1, py + ty * k * 1.1),
                   (px + nx * k * 0.8 - tx * k * 0.2, py + ny * k * 0.8 - ty * k * 0.2),
                   (px - nx * k * 0.8 - tx * k * 0.2, py - ny * k * 0.8 - ty * k * 0.2)], fill=col)
    elif kind == "close":
        t = max(2, int(s * 0.2))
        d.line((cx - s, cy - s, cx + s, cy + s), fill=col, width=t)
        d.line((cx - s, cy + s, cx + s, cy - s), fill=col, width=t)
    elif kind in ("left", "right"):
        m = -1 if kind == "left" else 1
        d.polygon([(cx - m * s * 0.5, cy - s), (cx - m * s * 0.5, cy + s), (cx + m * s * 0.6, cy)], fill=col)
    elif kind in ("vol-", "vol+", "mute", "muted"):
        ox = cx - s * 0.35
        d.polygon([(ox - s * 0.8, cy - s * 0.35), (ox - s * 0.35, cy - s * 0.35), (ox + s * 0.15, cy - s * 0.8),
                   (ox + s * 0.15, cy + s * 0.8), (ox - s * 0.35, cy + s * 0.35), (ox - s * 0.8, cy + s * 0.35)], fill=col)
        px, t = cx + s * 0.75, max(2, int(s * 0.16))
        if kind == "muted":
            r = s * 0.36
            d.line((px - r, cy - r, px + r, cy + r), fill=col, width=t)
            d.line((px - r, cy + r, px + r, cy - r), fill=col, width=t)
        elif kind == "mute":
            for i, r in enumerate((s * 0.35, s * 0.7)):
                d.arc((px - s * 0.55 - r, cy - r, px - s * 0.55 + r, cy + r), -50, 50, fill=col, width=t)
        else:
            d.rectangle((px - s * 0.38, cy - t / 2, px + s * 0.38, cy + t / 2), fill=col)
            if kind == "vol+":
                d.rectangle((px - t / 2, cy - s * 0.38, px + t / 2, cy + s * 0.38), fill=col)


def accent_from(cover):
    """Most striking colour of the cover, lifted to a bright, readable accent colour.

    Colours are grouped by hue; each pixel counts by saturation x brightness, so a large
    saturated area beats grey, black or white.
    """
    import colorsys
    if cover is None:
        return ACCENT_FALLBACK
    bins = {}
    for r, g, b in cover.convert("RGB").resize((24, 24)).getdata():
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s < 0.3 or v < 0.3:
            continue
        w = s * v
        acc = bins.setdefault(int(h * 12) % 12, [0.0, 0.0, 0.0, 0.0])
        acc[0] += w
        acc[1] += r * w
        acc[2] += g * w
        acc[3] += b * w
    if not bins:
        return ACCENT_FALLBACK
    total, rs, gs, bs = max(bins.values(), key=lambda a: a[0])
    if total < 8:  # not enough colour to build on
        return ACCENT_FALLBACK
    h, s, v = colorsys.rgb_to_hsv(rs / total / 255, gs / total / 255, bs / total / 255)
    r, g, b = colorsys.hsv_to_rgb(h, min(0.8, max(0.5, s)), max(0.9, v))
    col = (int(r * 255), int(g * 255), int(b * 255))
    # Lighten dark hues (deep blue, purple): the dark icon on the play button and the label on a
    # background of the same colour have to stay readable.
    for _ in range(10):
        if luminance(col) >= 0.4:
            break
        col = shade(col, 1.12)
    return col


def luminance(col):
    """Relative luminance (0-1) per WCAG."""
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = col
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def shade(col, f):
    """f < 1 darker, f > 1 lighter (towards white)."""
    if f <= 1:
        return tuple(int(c * f) for c in col)
    return tuple(int(c + (255 - c) * (f - 1)) for c in col)


def default_background():
    """Diagonal blue-purple gradient for when there is no cover (glass needs something to sit on)."""
    v = Image.linear_gradient("L").resize((W, H))
    g = ImageChops.add(v.point(lambda x: x // 2), v.transpose(Image.Transpose.ROTATE_90).resize((W, H)).point(lambda x: x // 2))
    a = Image.new("RGB", (W, H), (18, 26, 54))
    b = Image.new("RGB", (W, H), (58, 22, 62))
    return Image.composite(b, a, g)


__all__ = [
    "W", "H", "FONT", "FONT_BOLD", "BG", "FG", "FG_DIM", "FG_FAINT", "FG_SOFT", "BG_MAX_LUMA", "ACCENT",
    "ACCENT_FALLBACK", "ERROR", "_fonts", "font", "ellipsize", "wrap", "overflows", "fmt_time", "rounded",
    "icon", "accent_from", "luminance", "shade", "default_background",
]
