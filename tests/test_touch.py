"""Touch input: taps and long presses, with a fake input device (no hardware needed, but python-evdev is)."""
import os
import queue
import threading
import time
import unittest

from helpers import Base, m, needs_evdev


class Ev:
    def __init__(self, type_, code, value):
        self.type, self.code, self.value = type_, code, value


class FakeDevice:
    """Looks like an evdev device: a file descriptor to wait on, and read() for the queued events."""

    def __init__(self):
        self.r, self.w = os.pipe()
        self.pending = []

    @property
    def fd(self):
        return self.r

    def feed(self, events):
        self.pending += events
        os.write(self.w, b"x")

    def read(self):
        os.read(self.r, 1)
        events, self.pending = self.pending, []
        return events

    def close(self):
        os.close(self.r)
        os.close(self.w)


@needs_evdev
class LongPress(Base):
    def setUp(self):
        super().setUp()
        import evdev
        self.e = evdev.ecodes
        self.touches = []

    def tearDown(self):
        for t in self.touches:
            t.stop = True

    def start(self):
        import evdev
        t = m.Touch.__new__(m.Touch)
        threading.Thread.__init__(t, daemon=True)
        t.ev, t.dev, t.taps, t.raw_mode = evdev, FakeDevice(), queue.Queue(), False
        t.cal = m.DEFAULTS["calibration"]
        t.HOLD_SEC = 0.4                                   # shorter than the real 1.5 s, so the tests stay quick
        t.start()
        self.touches.append(t)
        return t

    def sample(self, x, y):
        return [Ev(self.e.EV_ABS, self.e.ABS_X, x), Ev(self.e.EV_ABS, self.e.ABS_Y, y), Ev(self.e.EV_SYN, 0, 0)]

    def down(self, t, x=2000, y=2000):
        t.dev.feed([Ev(self.e.EV_KEY, self.e.BTN_TOUCH, 1)] + self.sample(x, y) * 3)

    def up(self, t):
        t.dev.feed([Ev(self.e.EV_KEY, self.e.BTN_TOUCH, 0), Ev(self.e.EV_SYN, 0, 0)])

    def drain(self, t):
        out = []
        while not t.taps.empty():
            out.append(t.taps.get())
        return out

    def test_a_quick_tap_is_one_tap_and_no_long_press(self):
        t = self.start()
        self.down(t)
        time.sleep(0.15)
        self.up(t)
        time.sleep(0.6)
        events = self.drain(t)
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0]), 2)

    def test_a_resting_finger_gives_a_long_press_without_any_further_events(self):
        t = self.start()
        self.down(t)
        time.sleep(0.9)                                    # the controller says nothing more: the thread has to notice
        events = self.drain(t)
        self.assertEqual([len(e) for e in events], [2, 3])
        self.assertEqual(events[1][2], "hold")
        self.assertEqual(events[1][:2], events[0])         # at the place that was touched

    def test_a_finger_that_moves_away_is_not_a_long_press(self):
        t = self.start()
        self.down(t)
        time.sleep(0.15)
        t.dev.feed(self.sample(3200, 3200))
        time.sleep(0.6)
        self.assertEqual(len(self.drain(t)), 1)

    def test_a_long_press_fires_once_per_touch(self):
        t = self.start()
        self.down(t)
        time.sleep(1.4)
        self.assertEqual(len(self.drain(t)), 2)

    def test_the_next_touch_can_be_a_long_press_again(self):
        t = self.start()
        self.down(t)
        time.sleep(0.7)
        self.up(t)
        time.sleep(0.1)
        self.drain(t)
        self.down(t, 1500, 1500)
        time.sleep(0.7)
        self.assertEqual([len(e) for e in self.drain(t)], [2, 3])


if __name__ == "__main__":
    unittest.main()
