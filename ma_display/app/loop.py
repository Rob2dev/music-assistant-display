"""Starting the input and network threads, and the loop that shows frames and animates."""
import datetime
import logging
import math
import queue
import threading
import time

from ..hardware import Touch, wait_for_route
from ..sendspin import VIZ_BINS, VIZ_FPS
from ..widgets import MARQUEE_FPS

log = logging.getLogger("ma-display")


class MainLoop:
    def _start_touch(self):
        """Create the touch reader, retrying: with the early start the driver may not be up yet."""
        for attempt in range(120):
            try:
                Touch(self.cfg["touch_name"], self.cfg["calibration"], self.taps).start()
                return
            except Exception as e:
                if attempt == 0:
                    log.info("waiting for the touch device: %s", e)
                time.sleep(1)
        log.warning("touch not available")

    def _start_sendspin(self):
        """Start the Sendspin client once the network is up: it announces itself over mDNS, and an
        announcement made before there is an address would not be repeated."""
        if not wait_for_route():
            log.warning("no network after 120 s, starting the Sendspin client anyway")
        if self.ma:
            self.ma.refresh_async()  # the first attempt, at startup, usually ran before the network was up
        self.ss.start()

    def run(self, once=False):
        if not self.demo:
            self.sock.start()
            threading.Thread(target=self._start_sendspin, daemon=True).start()
            if self.ma:
                self.ma.refresh_async()  # fetch stations and logos up front
            time.sleep(0.5)  # wait for the first status
        if not once:
            threading.Thread(target=self._start_touch, daemon=True).start()
        last = None
        while True:
            img, screen, data = self.frame()
            on_player = screen == "player"
            marquees = self.r.marquees if on_player else []
            viz_box = self.r.viz_box if on_player else None
            idle = self.idle_anim if screen == "clock" else None
            if once:
                for strip, pos in self.animated_strips(img, marquees, viz_box, idle):
                    img.paste(strip, pos)
                self.sink.show(img)
                return
            b = img.tobytes()
            if b != last:
                # Put the moving parts (scrolling text, spectrum) into the full frame already;
                # otherwise they sit empty until the next strip update (flicker).
                shown = img.copy() if (marquees or viz_box or idle) else img
                for strip, pos in self.animated_strips(img, marquees, viz_box, idle):
                    shown.paste(strip, pos)
                self.sink.show(shown)
                if last is None:
                    log.info("first frame drawn")
                last = b
            # Wait for a tap, a state change or the next second; animate in the meantime.
            deadline = time.time() + (0.25 if (self.pressed is not None or self.toast) else 1.0)
            step = (1 / VIZ_FPS if viz_box else 1 / MARQUEE_FPS if marquees
                    else 1 / max(1, self.cfg["idle_fps"]) if idle else 0.1)
            while time.time() < deadline:
                try:
                    tap = self.taps.get(timeout=max(0.01, min(step, deadline - time.time())))
                    if len(tap) == 3:
                        self.handle_hold(tap[0], tap[1], screen)
                    else:
                        self.handle_tap(tap[0], tap[1], screen, data)
                    break
                except queue.Empty:
                    pass
                for strip, (x, y) in self.animated_strips(img, marquees, viz_box, idle):
                    self.sink.show_region(strip, x, y)
                if self.dirty.is_set():
                    self.dirty.clear()
                    break

    def animated_strips(self, base, marquees, viz_box, idle=None):
        """(strip, position) for everything that moves: scrolling lines, the spectrum and the idle clock's hands."""
        now = time.time()
        texts = {m.text for m in marquees}
        if texts != set(self.marquee_t0):
            # New text starts with the pause at the front again.
            self.marquee_t0 = {t: self.marquee_t0.get(t, now) for t in texts}
        strips = [(m.render(base, now - self.marquee_t0[m.text]), m.box[:2]) for m in marquees]
        if viz_box:
            self.spectrum.update(self.current_spectrum())
            strips.append((self.spectrum.render(base, viz_box, self.r.accent), viz_box[:2]))
        if idle:
            strips.append((idle.hands(datetime.datetime.now()), idle.face_box[:2]))
        return strips

    def current_spectrum(self):
        if self.demo:
            t = time.time()
            return [int(30000 * (0.55 + 0.45 * math.sin(t * 3 + i * 0.5)) * (1 - i / 40)) for i in range(VIZ_BINS)]
        return self.ss.current_spectrum()


__all__ = ["MainLoop"]
