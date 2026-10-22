"""Picking the language and looking texts up: tr(), set_language(), fmt_date()."""
import os

from .strings import STRINGS


_lang = "en"


def set_language(code):
    """Pick the UI language: explicit config value, else $LANG (e.g. nl_NL.UTF-8), else English."""
    global _lang
    code = (code or os.environ.get("LC_ALL") or os.environ.get("LANG") or "en")[:2].lower()
    _lang = code if code in STRINGS else "en"
    return _lang


def tr(key, **kw):
    s = STRINGS[_lang].get(key)
    if s is None:
        s = STRINGS["en"][key]
    return s.format(**kw) if kw else s


def current_language():
    return _lang


def fmt_date(now):
    return tr("date", weekday=tr("weekdays")[now.weekday()], day=now.day, month=tr("months")[now.month - 1])


__all__ = [
    "_lang", "set_language", "tr", "fmt_date", "current_language",
]
