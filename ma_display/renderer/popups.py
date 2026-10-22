"""The panels over the player screen: volume and the power menu."""

from PIL import Image, ImageDraw

from ..gfx import ERROR, FG, FG_DIM, FG_FAINT, H, W, font, icon
from ..i18n import tr
from ..layout import POPUP, POPUP_BAR, POPUP_BUTTONS, POWER_PANEL, POWER_ROWS


class PopupScreens:
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


__all__ = ["PopupScreens"]
