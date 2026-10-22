"""The settings that can be changed on the screen: their definitions, validation and settings.json."""
import json
import logging
import os
import socket

from .i18n import tr
from .strings import NAMES

log = logging.getLogger("ma-display")


# Settings that can be changed on the screen (menu -> Settings). They are saved in
# state_dir/settings.json, which the service may write (it cannot write /etc/ma-display/config.json, and
# that file may hold the token), and they win over config.json. The last entry is an action, not a setting.
LANG_NAMES = dict(NAMES)  # each language file says what it is called in itself (strings/<code>.py)


LANGUAGE_CODES = list(LANG_NAMES)


SETTING_DEFS = [
    {"tab": "general", "key": "language", "label": "set_language", "kind": "cycle", "values": LANGUAGE_CODES},
    {"tab": "general", "key": "volume_step", "label": "set_volume_step", "kind": "cycle", "values": [1, 2, 5, 10]},
    {"tab": "general", "label": "set_reset", "kind": "action"},
    {"tab": "display", "key": "show_cover", "label": "set_cover", "kind": "toggle"},
    {"tab": "display", "key": "show_analyser", "label": "set_analyser", "kind": "toggle"},
    {"tab": "display", "key": "idle_style", "label": "set_idle_style", "kind": "cycle", "values": ["analog", "digital"]},
    {"tab": "display", "key": "clock_after_min", "label": "set_idle_after", "kind": "step", "step": 1, "min": 1,
     "max": 60},
    {"tab": "display", "key": "night_after_min", "label": "set_night_after", "kind": "step", "step": 15, "min": 0,
     "max": 240},
]


SETTING_KEYS = tuple(sd["key"] for sd in SETTING_DEFS if "key" in sd)


def _valid_setting(key, value):
    sd = next((s for s in SETTING_DEFS if s.get("key") == key), None)
    if sd is None:
        return False
    if sd["kind"] == "toggle":
        return isinstance(value, bool)
    if isinstance(value, bool):  # True == 1 would otherwise pass for the numeric settings
        return False
    if sd["kind"] == "cycle":
        return value in sd["values"]
    return isinstance(value, (int, float)) and sd["min"] <= value <= sd["max"]


def settings_path(state_dir):
    return os.path.join(state_dir, "settings.json")


def load_settings(state_dir):
    """The saved settings; anything unknown or out of range is ignored."""
    try:
        with open(settings_path(state_dir)) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    good = {k: v for k, v in raw.items() if _valid_setting(k, v)} if isinstance(raw, dict) else {}
    if raw != good:
        log.warning("ignoring invalid entries in %s", settings_path(state_dir))
    return good


def save_settings(state_dir, settings):
    """Write the settings atomically; an empty dict removes the file. Returns False when that fails."""
    path = settings_path(state_dir)
    try:
        if not settings:
            if os.path.exists(path):
                os.remove(path)
            return True
        os.makedirs(state_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(settings, f, indent=2)
        os.replace(tmp, path)
        return True
    except OSError as e:
        log.warning("saving settings failed: %s", e)
        return False


def settings_rows(tab):
    """The rows of one settings tab."""
    return [sd for sd in SETTING_DEFS if sd["tab"] == tab]


def setting_text(sd, value):
    """How a setting's value reads on screen."""
    key = sd["key"]
    if key == "language":
        return LANG_NAMES.get(value, str(value))
    if key == "idle_style":
        return tr("style_" + value)
    if key == "night_after_min" and not value:
        return tr("off")
    if key in ("clock_after_min", "night_after_min"):
        return f"{value:g} min"
    return str(value)


def system_info(ma_connected):
    """(label key, value) lines for the Info tab."""
    ip = "-"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0] if s.getsockname()[0] != "0.0.0.0" else "-"
    except OSError:
        pass
    finally:
        s.close()
    try:
        with open("/proc/uptime") as f:
            up = int(float(f.read().split()[0]))
    except (OSError, ValueError):
        up = 0
    days, rest = divmod(up, 86400)
    hours, rest = divmod(rest, 3600)
    uptime = (f"{days} d " if days else "") + (f"{hours} h " if days or hours else "") + f"{rest // 60} min"
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            temp = f"{int(f.read()) / 1000:.0f} °C"
    except (OSError, ValueError):
        temp = "-"
    return [("info_host", socket.gethostname()), ("info_ip", ip), ("info_uptime", uptime),
            ("info_ma", tr("ma_connected") if ma_connected else tr("ma_disconnected")), ("info_temp", temp)]


__all__ = [
    "LANG_NAMES", "LANGUAGE_CODES", "SETTING_DEFS", "SETTING_KEYS", "_valid_setting", "settings_path",
    "load_settings", "save_settings", "settings_rows", "setting_text", "system_info",
]
