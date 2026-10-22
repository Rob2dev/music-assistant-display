"""The background, the frosted-glass panels and the overlay every screen shares."""

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageStat

from ..gfx import ACCENT_FALLBACK, BG_MAX_LUMA, ERROR, FG, H, W, accent_from, default_background, font, shade


class RendererBase:
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


__all__ = ["RendererBase"]
