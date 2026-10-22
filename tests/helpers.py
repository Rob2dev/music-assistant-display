"""Shared setup for the tests.

The program reads its config path from $MA_DISPLAY_CONFIG when it is imported, so a throw-away config and state
directory are set up here, before `ma_display` is imported. Tests that draw text need the DejaVu fonts and are
skipped without them; the touch test needs python-evdev.
"""
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest

TMP = tempfile.mkdtemp(prefix="ma_display_tests_")
os.environ["MA_DISPLAY_CONFIG"] = os.path.join(TMP, "config.json")
with open(os.environ["MA_DISPLAY_CONFIG"], "w") as f:
    json.dump({"state_dir": TMP, "ma_token": "secret", "clock_after_min": 7}, f)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import ma_display as m  # noqa: E402

FONTS_OK = os.path.exists(m.FONT) and os.path.exists(m.FONT_BOLD)
needs_fonts = unittest.skipUnless(FONTS_OK, "the DejaVu fonts are not installed")
try:
    import evdev  # noqa: F401
    HAVE_EVDEV = True
except ImportError:
    HAVE_EVDEV = False
needs_evdev = unittest.skipUnless(HAVE_EVDEV, "python-evdev is not installed")


def state_file(name):
    return os.path.join(TMP, name)


class Base(unittest.TestCase):
    """Every test starts from the same state: English and no saved settings."""

    def setUp(self):
        m.set_language("en")
        for name in ("settings.json", "last_played.json", "last_logo.png"):
            if os.path.exists(state_file(name)):
                os.remove(state_file(name))


def make_app(**over):
    """An App without its threads, sockets and display: just the state the tap handlers work on."""
    a = m.App.__new__(m.App)
    a.cfg = m.load_config()
    a.base_cfg = m.load_config(use_settings=False)
    a.settings = m.load_settings(TMP)
    a.demo = False
    a.toast, a.toast_until = None, 0
    a.pressed, a.pressed_until = None, 0
    a.awake_until = 0
    a.power_until = 0
    a.settings_until = 0
    a.set_tab, a.set_picker = "general", False
    a.stations_until, a.st_tab, a.st_page = 0, "fav", 0
    a.room_confirm, a.room_confirm_until = None, 0
    a.dirty = threading.Event()
    a.sock = types.SimpleNamespace(wake=threading.Event())
    a.ss = types.SimpleNamespace(viz_enabled=True)
    a.idle = m.IdleClock()
    a.ran = []
    a.run_power = lambda kind: a.ran.append(kind)
    a.__dict__.update(over)
    return a


def open_settings(app, tab="general"):
    app.settings_until = time.time() + 60
    app.set_tab = tab
    return app


def station(name, uri="library://radio/1", logo=None):
    return {"name": name, "uri": uri, "logo": logo}
