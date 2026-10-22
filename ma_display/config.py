"""The defaults, config.json and the touch calibration."""
import copy
import json
import os
import socket

from .settings import load_settings


CONFIG_PATH = os.environ.get("MA_DISPLAY_CONFIG", "/etc/ma-display/config.json")


HOSTNAME = socket.gethostname()


DEFAULTS = {
    "control_socket": "/run/sendspin-cli/control.sock",
    # How Music Assistant lists this display. sendspin-cli names the player after the hostname
    # by default; override "ma_player_name" if you gave the player another name.
    "sendspin_name": HOSTNAME + " display",
    "sendspin_port": 8929,
    "state_dir": "/var/lib/ma-display",
    # Music Assistant API, only needed to pick stations: set the URL (e.g. "http://192.168.1.10:8095")
    # and the token (ma-display-set-token). Without both the station picker is disabled.
    "ma_url": "",
    "ma_token": "",
    "ma_player_name": HOSTNAME,
    # Framebuffer of the SPI panel. "auto" looks it up by the driver name in "fb_name": the number
    # depends on the order the drivers load in (the HDMI firmware framebuffer can still be fb0).
    # Set a path such as "/dev/fb1" to pin it.
    "fb": "auto",
    "fb_name": "ili9486drmfb",
    "touch_name": "ADS7846 Touchscreen",
    "volume_step": 5,
    # What the power menu runs (long-press the clock). The service user needs permission to run
    # these: see polkit/50-ma-display-power.rules.
    "shutdown_command": ["systemctl", "poweroff"],
    "restart_command": ["systemctl", "reboot"],
    # Minutes without playback before the clock screen appears.
    "clock_after_min": 5,
    # Idle screen: "analog" (clock face, date and a tile for the last station, drifting slowly) or
    # "digital" (the plain big clock). After "night_after_min" minutes without playback it turns into a
    # dim digital time (0 = never). "idle_fps" is how often the hands are redrawn.
    "idle_style": "analog",
    "night_after_min": 60,
    "idle_fps": 4,
    # Cover art (also the blurred background) and the spectrum analyser on the player screen.
    "show_cover": True,
    "show_analyser": True,
    # UI language: en, nl, fr or de. Empty = derive from $LANG, else English.
    "language": "",
    # Raw touch values -> screen coordinates (after the optional swap). Measured with --calibrate.
    "calibration": {"swap_xy": True, "x_min": 3936, "x_max": 227, "y_min": 268, "y_max": 3880},
}


def load_config(use_settings=True):
    cfg = copy.deepcopy(DEFAULTS)
    try:
        with open(CONFIG_PATH) as f:
            user = json.load(f)
        cal = user.pop("calibration", None)
        cfg.update(user)
        if cal:
            cfg["calibration"].update(cal)
    except FileNotFoundError:
        pass
    if use_settings:
        cfg.update(load_settings(cfg["state_dir"]))
    return cfg


def save_calibration(cal):
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    data["calibration"] = cal
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    # Keep the owner and mode of an existing file: it may hold the MA token (set-token makes it
    # root:sendspin-cli 0640), so a calibration must not make it world-readable.
    try:
        st = os.stat(CONFIG_PATH)
        os.chmod(tmp, st.st_mode & 0o777)
        if hasattr(os, "chown"):
            os.chown(tmp, st.st_uid, st.st_gid)
    except FileNotFoundError:
        os.chmod(tmp, 0o640)
    except PermissionError:
        pass  # not root: the file stays ours, which is the best we can do
    os.replace(tmp, CONFIG_PATH)


__all__ = [
    "CONFIG_PATH", "HOSTNAME", "DEFAULTS", "load_config", "save_calibration",
]
