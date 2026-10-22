"""The state the application keeps, and its constructor."""
import logging
import queue
import threading
import time

from ..config import load_config
from ..demo import demo_cover
from ..music_assistant import MAClient
from ..player_socket import PlayerSocket
from ..renderer import Renderer
from ..sendspin import SendspinDisplay, VIZ_BINS
from ..settings import load_settings
from ..widgets import IdleClock, SpectrumViz

log = logging.getLogger("ma-display")


class AppBase:
    def __init__(self, cfg, sink, demo=False):
        self.cfg = cfg
        self.sink = sink
        self.demo = demo
        self.r = Renderer()
        self.taps = queue.Queue()
        self.dirty = threading.Event()
        self.toast = None
        self.toast_until = 0
        self.pressed = None
        self.pressed_until = 0
        self.last_active = time.time()
        self.started = time.time()
        self.awake_until = 0
        self.popup_until = 0
        self.power_until = 0
        self.power_busy = None  # "shutdown" / "restart" once chosen
        self.idle = IdleClock()
        self.settings = load_settings(cfg["state_dir"])  # what was changed on screen (also in cfg)
        self.base_cfg = load_config(use_settings=False)  # what "reset to defaults" goes back to
        self.settings_until = 0
        self.set_tab = "general"
        self.set_picker = False  # the language list is open
        self.room_confirm = None  # {"id", "name", "add"} while the add/remove confirmation is open
        self.room_confirm_until = 0
        self.idle_anim = None  # the IdleClock while its hands have to be redrawn, else None
        self.stations_until = 0
        self.marquee_t0 = {}
        self.spectrum = SpectrumViz(VIZ_BINS)
        self.st_tab = None
        self.st_page = 0
        self.optimistic = {}
        self.optimistic_until = 0
        self.mismatch_since = None
        self.last_switch = 0
        self.remembered = (None, 0)  # (title, time) of the last remember_async
        self._demo_cover = demo_cover() if demo else None
        if not demo:
            self.sock = PlayerSocket(cfg["control_socket"], self.dirty.set)
            self.ss = SendspinDisplay(cfg, self.dirty.set)
            self.ss.viz_enabled = cfg["show_analyser"]
        # Picking stations needs an MA token.
        self.ma = MAClient(cfg, self.dirty.set) if cfg["ma_token"] and cfg["ma_url"] and not demo else None
        if cfg["ma_token"] and not cfg["ma_url"] and not demo:
            log.warning("ma_token is set but ma_url is not: station picker disabled")


__all__ = ["AppBase"]
