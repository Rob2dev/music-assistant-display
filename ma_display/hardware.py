"""The display (framebuffer), the touch panel and the network check."""
import glob
import logging
import os
import socket
import threading
import time

from PIL import Image, ImageChops

from .gfx import H, W

log = logging.getLogger("ma-display")


# ---------------------------------------------------------------- framebuffer

_LUT_R = [(v >> 3) << 3 for v in range(256)]


_LUT_GH = [v >> 5 for v in range(256)]


_LUT_GL = [((v >> 2) & 7) << 5 for v in range(256)]


_LUT_B = [v >> 3 for v in range(256)]


def to_rgb565(img):
    """RGB image -> little-endian RGB565 bytes (Pillow has no packer for this)."""
    r, g, b = img.split()
    hi = ImageChops.add(r.point(_LUT_R), g.point(_LUT_GH))
    lo = ImageChops.add(g.point(_LUT_GL), b.point(_LUT_B))
    return Image.merge("LA", (lo, hi)).tobytes()


def find_framebuffer(spec, name):
    """The /dev/fbN to draw on, or None if it is not there (yet)."""
    if spec != "auto":
        return spec if os.path.exists(spec) else None
    for sys_dir in sorted(glob.glob("/sys/class/graphics/fb[0-9]*")):
        try:
            with open(sys_dir + "/name") as f:
                if f.read().strip() == name:
                    return "/dev/" + os.path.basename(sys_dir)
        except OSError:
            pass
    return None


def open_framebuffer(cfg, wait=120):
    """Open the panel's framebuffer, waiting for the driver instead of dying: the service starts early."""
    deadline = time.time() + wait
    warned = failed = False
    while True:
        path = find_framebuffer(cfg["fb"], cfg["fb_name"])
        if path:
            try:
                fb = Framebuffer(path)
                log.info("drawing on %s", path)
                return fb
            except OSError as e:
                if not failed:  # e.g. a permission problem would otherwise look like "still waiting"
                    log.warning("cannot open %s yet: %s", path, e)
                    failed = True
        if time.time() > deadline:
            raise RuntimeError(f"no framebuffer '{cfg['fb']}' ({cfg['fb_name']}) after {wait} s")
        if not warned:
            log.info("waiting for the display framebuffer")
            warned = True
        time.sleep(0.5)


def wait_for_route(timeout=120):
    """Wait until the Pi has an IPv4 address (a UDP connect sends nothing but needs a route)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))
            if s.getsockname()[0] != "0.0.0.0":
                return True
        except OSError:
            pass
        finally:
            s.close()
        time.sleep(1)
    return False


class Framebuffer:
    # The service puts tty1 into graphics mode (ExecStartPre, as root) so the console
    # does not draw over the picture.

    def __init__(self, path):
        base = "/sys/class/graphics/" + os.path.basename(path)
        self.stride = int(open(base + "/stride").read())
        self.fd = os.open(path, os.O_RDWR)
        self._activate()

    def _activate(self):
        """Switch the panel's display pipeline on.

        When the panel driver loads before vc4 (early module loading), the console stays on the HDMI
        framebuffer and nothing ever enables the panel's DRM pipeline: we would draw into the buffer but
        the panel shows nothing. Re-applying the current video mode with FB_ACTIVATE_FORCE makes the
        driver do its modeset. Harmless when the pipeline is already on.
        """
        import fcntl
        try:
            buf = bytearray(160)  # struct fb_var_screeninfo
            fcntl.ioctl(self.fd, 0x4600, buf)  # FBIOGET_VSCREENINFO
            buf[84:88] = (int.from_bytes(buf[84:88], "little") | 0x80).to_bytes(4, "little")  # activate |= FORCE
            fcntl.ioctl(self.fd, 0x4601, buf)  # FBIOPUT_VSCREENINFO: set_par -> modeset
        except OSError as e:
            log.warning("could not activate the framebuffer: %s", e)

    def show(self, img):
        data = to_rgb565(img)
        if self.stride != W * 2:
            pad = bytes(self.stride - W * 2)
            data = b"".join(data[y * W * 2:(y + 1) * W * 2] + pad for y in range(H))
        os.pwrite(self.fd, data, 0)

    def show_region(self, img, x0, y0):
        """Update just a strip (scrolling text): far less data over SPI."""
        data = to_rgb565(img)
        row = img.width * 2
        for y in range(img.height):
            os.pwrite(self.fd, data[y * row:(y + 1) * row], (y0 + y) * self.stride + x0 * 2)

    def close(self):
        os.close(self.fd)


class PngSink:
    def __init__(self, path):
        self.path = path

    def show(self, img):
        img.save(self.path)

    def show_region(self, img, x0, y0):
        pass

    def close(self):
        pass


# ---------------------------------------------------------------- touch

class Touch(threading.Thread):
    """Reads the touch panel and puts taps (x, y) on a queue.

    A touch held still for HOLD_SEC also puts a long press (x, y, "hold") on the queue.
    """

    HOLD_SEC = 1.5   # how long a touch has to rest to count as a long press
    HOLD_SLOP = 200  # raw units the finger may drift meanwhile

    def __init__(self, name, cal, taps, raw_mode=False):
        super().__init__(daemon=True)
        import evdev
        self.ev = evdev
        self.dev = next((evdev.InputDevice(p) for p in evdev.list_devices()
                         if evdev.InputDevice(p).name == name), None)
        if self.dev is None:
            raise RuntimeError(f"touch device '{name}' not found")
        self.cal = cal
        self.taps = taps
        self.raw_mode = raw_mode

    def map(self, rx, ry):
        c = self.cal
        a, b = (ry, rx) if c["swap_xy"] else (rx, ry)
        x = (a - c["x_min"]) / (c["x_max"] - c["x_min"]) * W
        y = (b - c["y_min"]) / (c["y_max"] - c["y_min"]) * H
        return max(0, min(W - 1, int(x))), max(0, min(H - 1, int(y)))

    def run(self):
        import select
        e = self.ev.ecodes
        rx = ry = 0
        down = False
        pressed = False
        held = False
        t_down = 0.0
        start = (0, 0)
        samples = []
        while True:
            # Wake up regularly even when no events come in: a long press has to fire while the
            # finger rests, which is exactly when the controller may stay quiet.
            if select.select([self.dev.fd], [], [], 0.1)[0]:
                try:
                    events = list(self.dev.read())
                except BlockingIOError:
                    events = []
                for ev in events:
                    if ev.type == e.EV_ABS:
                        if ev.code == e.ABS_X:
                            rx = ev.value
                        elif ev.code == e.ABS_Y:
                            ry = ev.value
                    elif ev.type == e.EV_KEY and ev.code == e.BTN_TOUCH:
                        down = bool(ev.value)
                    elif ev.type == e.EV_SYN:
                        if down:
                            samples.append((rx, ry))
                            if not pressed and len(samples) >= 3:
                                pressed = True
                                # The first samples are often noisy: take the median of what we have.
                                xs = sorted(s[0] for s in samples)
                                ys = sorted(s[1] for s in samples)
                                start = (xs[len(xs) // 2], ys[len(ys) // 2])
                                t_down, held = time.time(), False
                                log.debug("tap raw=%s", start)
                                self.taps.put(start if self.raw_mode else self.map(*start))
                        else:
                            pressed = held = False
                            samples = []
            if (pressed and not held and not self.raw_mode and time.time() - t_down >= self.HOLD_SEC
                    and abs(rx - start[0]) <= self.HOLD_SLOP and abs(ry - start[1]) <= self.HOLD_SLOP):
                held = True
                log.debug("long press raw=%s", start)
                self.taps.put((*self.map(*start), "hold"))


__all__ = [
    "_LUT_R", "_LUT_GH", "_LUT_GL", "_LUT_B", "to_rgb565", "find_framebuffer", "open_framebuffer",
    "wait_for_route", "Framebuffer", "PngSink", "Touch",
]
