"""Touch calibration: tap three crosshairs."""
import json
import queue
import time

from PIL import ImageDraw

from .config import save_calibration
from .gfx import ACCENT, FG
from .hardware import Touch
from .i18n import tr
from .renderer import Renderer


def calibrate(cfg, sink):
    taps = queue.Queue()
    Touch(cfg["touch_name"], cfg["calibration"], taps, raw_mode=True).start()
    r = Renderer()
    targets = [(40, 40), (440, 40), (40, 280)]
    raws = []
    for i, (tx, ty) in enumerate(targets):
        img = r.message(tr("calibrate_title"), [tr("calibrate_step", n=i + 1)])
        d = ImageDraw.Draw(img)
        d.line((tx - 16, ty, tx + 16, ty), fill=ACCENT, width=3)
        d.line((tx, ty - 16, tx, ty + 16), fill=ACCENT, width=3)
        d.ellipse((tx - 6, ty - 6, tx + 6, ty + 6), outline=FG, width=2)
        sink.show(img)
        while not taps.empty():
            taps.get()
        raws.append(taps.get())
        time.sleep(0.6)
        while not taps.empty():
            taps.get()
    (x0, y0), (x1, y1), (x2, y2) = raws
    swap = abs(y1 - y0) > abs(x1 - x0)  # which raw axis changes on horizontal movement?
    h0, h1 = (y0, y1) if swap else (x0, x1)
    v0, v2 = (x0, x2) if swap else (y0, y2)
    sx = (h1 - h0) / 400
    sy = (v2 - v0) / 240
    cal = {"swap_xy": swap,
           "x_min": round(h0 - sx * 40), "x_max": round(h0 + sx * 440),
           "y_min": round(v0 - sy * 40), "y_max": round(v0 + sy * 280)}
    save_calibration(cal)
    print("calibration saved:", cal, "raw:", raws)
    sink.show(r.message(tr("calibrate_done"), [json.dumps(cal)]))


__all__ = [
    "calibrate",
]
