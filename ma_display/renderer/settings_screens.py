"""The settings screen and the language list."""

from PIL import ImageDraw

from ..gfx import FG, FG_DIM, FG_FAINT, ellipsize, font, icon
from ..i18n import tr
from ..layout import (
    INFO_ROW_H, LIST_X0, LIST_X1, PICKER_H, PICKER_PITCH, PICKER_W, PICKER_X, SET_ROW_H, SET_ROW_PITCH,
    SET_STEP, SET_TAB_NAMES, SET_Y0, ST_HEADER, SWITCH_H, SWITCH_MARGIN, SWITCH_W, tab_ranges,
)
from ..settings import LANGUAGE_CODES, LANG_NAMES, setting_text, settings_rows


class SettingsScreens:
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


__all__ = ["SettingsScreens"]
