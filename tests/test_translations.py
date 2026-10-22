"""Translations: every language is complete and consistent, and (with the fonts) every text fits its place.

When you add a language, these are the tests that tell you what is missing or too long.
"""
import datetime
import os
import unittest

from helpers import Base, m, needs_fonts

PLACEHOLDERS = ("{pin}", "{n}", "{name}", "{day}", "{month}", "{weekday}")


class Completeness(Base):
    def test_every_language_has_every_key(self):
        keys = set(m.STRINGS["en"])
        for lang, strings in m.STRINGS.items():
            self.assertEqual(set(strings), keys, "%s: missing %s, extra %s" % (
                lang, sorted(keys - set(strings)), sorted(set(strings) - keys)))

    def test_placeholders_match_english_and_formats_are_valid(self):
        for lang, strings in m.STRINGS.items():
            for key, english in m.STRINGS["en"].items():
                if not isinstance(english, str):
                    continue
                found = lambda s: sorted(p for p in PLACEHOLDERS if p in s)
                self.assertEqual(found(strings[key]), found(english), "%s.%s" % (lang, key))
                strings[key].format(pin="1", n=1, name="x", day=1, month="m", weekday="w")

    def test_weekdays_and_months(self):
        for lang, strings in m.STRINGS.items():
            self.assertEqual(len(strings["weekdays"]), 7, lang)
            self.assertEqual(len(strings["months"]), 12, lang)

    def test_every_language_has_a_name_for_the_picker(self):
        self.assertEqual(set(m.LANG_NAMES), set(m.STRINGS))


class Dates(Base):
    def test_dates_follow_the_language(self):
        day = datetime.datetime(2026, 10, 21, 18, 42)
        expected = {
            "en": "Wednesday, October 21", "nl": "woensdag 21 oktober", "fr": "mercredi 21 octobre",
            "de": "Mittwoch, 21. Oktober", "es": "miércoles 21 de octubre", "it": "mercoledì 21 ottobre",
            "pt": "quarta-feira, 21 de outubro", "pl": "środa, 21 października", "sv": "onsdag 21 oktober",
            "cs": "středa 21. října",
        }
        self.assertEqual(set(expected), set(m.STRINGS))
        for lang, text in expected.items():
            m.set_language(lang)
            self.assertEqual(m.fmt_date(day), text, lang)


class Selection(Base):
    def test_language_is_derived_from_the_environment(self):
        saved = {k: os.environ.get(k) for k in ("LANG", "LC_ALL")}
        try:
            os.environ.pop("LC_ALL", None)
            for env, want in (("es_ES.UTF-8", "es"), ("pt_BR.UTF-8", "pt"), ("sv_SE", "sv"), ("cs_CZ.UTF-8", "cs"),
                              ("pl_PL.UTF-8", "pl"), ("it_IT", "it"), ("ru_RU.UTF-8", "en"), ("", "en")):
                os.environ["LANG"] = env
                self.assertEqual(m.set_language(""), want, env)
            self.assertEqual(m.set_language("nl"), "nl")              # an explicit value beats the environment
            self.assertEqual(m.set_language("xx"), "en")              # unknown falls back to English
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_a_missing_key_falls_back_to_english(self):
        saved = m.STRINGS["nl"].pop("retrying")
        try:
            m.set_language("nl")
            self.assertEqual(m.tr("retrying"), m.STRINGS["en"]["retrying"])
        finally:
            m.STRINGS["nl"]["retrying"] = saved


@needs_fonts
class Fit(Base):
    """Every text has a fixed amount of room on the 480x320 screen. Measured with the real font."""

    @classmethod
    def setUpClass(cls):
        from PIL import Image, ImageDraw
        cls.draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))

    def width(self, text, size, bold=True):
        return self.draw.textlength(text, font=m.font(size, bold))

    def check(self, problems, lang, what, width, limit):
        if width > limit:
            problems.append("%s: %s is %.0f px, room for %d" % (lang, what, width, limit))

    def test_tabs_fit_and_the_hit_areas_match(self):
        problems = []
        for lang in m.STRINGS:
            m.set_language(lang)
            stations, settings = m.tab_ranges(m.ST_TAB_NAMES), m.tab_ranges(m.SET_TAB_NAMES)
            for ranges, limit in ((stations, m.ST_HEADER["left"][0] - 4), (settings, 424)):
                areas = list(ranges.values())
                self.assertTrue(all(a[1] == b[0] for a, b in zip(areas, areas[1:])), "%s: gaps/overlap" % lang)
                self.check(problems, lang, "last tab end", areas[-1][1], limit)
                for name, (x0, x1) in ranges.items():
                    self.check(problems, lang, "tab " + name, x0 + 12 + self.width(m.tr("tab_" + name), 16), x1 - 8)
        self.assertEqual(problems, [])

    def test_settings_rows(self):
        problems = []
        for lang in m.STRINGS:
            m.set_language(lang)
            for sd in m.SETTING_DEFS:
                self.check(problems, lang, sd["label"], self.width(m.tr(sd["label"]), 16),
                           410 if sd["kind"] == "action" else 250)
                if sd["kind"] == "step":      # the value sits between the - and + buttons: 88 px
                    for v in (0, sd["min"], sd["max"]):
                        self.check(problems, lang, "value of " + sd["key"], self.width(m.setting_text(sd, v), 15), 88)
            for name in m.LANG_NAMES.values():
                self.check(problems, lang, "language name " + name, self.width(name, 17), 190)
        self.assertEqual(problems, [])

    def test_menu_and_status_texts(self):
        problems = []
        for lang in m.STRINGS:
            m.set_language(lang)
            for key in ("menu_settings", "power_restart", "power_shutdown", "power_cancel"):
                self.check(problems, lang, key, self.width(m.tr(key), 18), 240)
            for st in ("playing", "paused", "idle", "stopped", "off", "unavailable", "starting"):
                # status label + LIVE badge must fit the text area (the clock is dropped when they collide with it)
                total = self.width(m.tr("status_" + st), 13) + 8 + self.width(m.tr("live"), 11) + 24
                self.check(problems, lang, "status+LIVE " + st, total, 248)
            for key in ("failed", "play_failed", "resume_failed", "retrying", "power_failed", "room_failed",
                        "settings_failed", "settings_reset_done"):
                self.check(problems, lang, "toast " + key, self.width(m.tr(key), 15), 420)
            for key in ("err_no_ma", "err_no_player"):
                self.check(problems, lang, key, self.width(m.tr(key), 12), 380)
            self.check(problems, lang, "pairing bar", self.width(m.tr("err_pairing", pin="123456"), 12), 380)
            for key in ("stations_loading", "stations_none", "stations_failed", "rooms_none", "rooms_loading",
                        "rooms_failed"):
                self.check(problems, lang, key, self.width(m.tr(key), 16, False), 440)
            self.check(problems, lang, "retry hint", self.width(m.tr("tap_retry"), 14) + 36, 440)
            self.check(problems, lang, "rooms offline", self.width(m.tr("rooms_offline"), 13, False), 100)
            self.check(problems, lang, "info labels", max(
                self.width(m.tr(k), 14, False) for k in ("info_host", "info_ip", "info_uptime", "info_ma", "info_temp")),
                190)
        self.assertEqual(problems, [])

    def test_idle_screen_texts(self):
        problems = []
        for lang in m.STRINGS:
            m.set_language(lang)
            for name in m.tr("weekdays"):   # the idle screen shrinks the weekday down to 17 px if needed
                self.check(problems, lang, "weekday " + name, min(self.width(name, s) for s in (20, 19, 18, 17)), 156)
            for month in m.tr("months"):
                self.check(problems, lang, "date " + month, self.width(m.tr("date_short", day=28, month=month), 17, False), 156)
                self.check(problems, lang, "clock date " + month,
                           self.width(m.fmt_date(datetime.datetime(2026, 5, 28)), 22, False), 440)
            lines = m.wrap(self.draw, m.tr("tap_resume"), m.font(12), 92, 2)
            if m.overflows(lines, m.tr("tap_resume")):
                problems.append("%s: the tile hint is cut off: %s" % (lang, lines))
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
