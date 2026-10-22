"""What a tap does: playback and volume commands, resuming, rooms, power."""
import logging
import subprocess
import threading
import time

from ..i18n import tr

log = logging.getLogger("ma-display")


class Actions:
    def show_toast(self, text, secs=2.0):
        self.toast, self.toast_until = text, time.time() + secs

    def action(self, btn, data, value=None):
        a = (data or {}).get("attributes", {})
        if btn == "vol_set":
            # Pick a volume directly on the bar; also unmutes.
            self.optimistic = {"attributes": {"volume_level": value / 100, "is_volume_muted": False}}
            self.optimistic_until = time.time() + 3
            try:
                if not self.demo:
                    for line in ([f"vol {value}"] + (["mute off"] if a.get("is_volume_muted") else [])):
                        ok, text = self.sock.cmd(line)
                        if not ok:
                            raise RuntimeError(text)
                    self.sock.wake.set()
            except Exception as ex:
                self.optimistic = {}
                self.show_toast(tr("failed"))
                log.warning("setting volume failed: %s", ex)
            return
        self.pressed, self.pressed_until = btn, time.time() + 0.25
        playing = (data or {}).get("state") == "playing"
        last = self.ma.last_played() if self.ma and not self.demo else None
        if btn == "play" and not playing and last and not a.get("media_title"):
            # Nothing in the queue: restart the last played item right away.
            self.resume(last)
            return
        try:
            if btn == "play":
                line = "pause" if playing else "play"
                self.optimistic = {"state": "paused" if playing else "playing"}
            elif btn in ("prev", "next"):
                line = btn
                self.optimistic = {}
            elif btn in ("vol-", "vol+"):
                cur = round((a.get("volume_level") or 0) * 100)
                vol = min(100, max(0, cur + self.cfg["volume_step"] * (1 if btn == "vol+" else -1)))
                line = f"vol {vol}"
                self.optimistic = {"attributes": {"volume_level": vol / 100}}
            elif btn == "mute":
                muted = bool(a.get("is_volume_muted"))
                line = "mute off" if muted else "mute on"
                self.optimistic = {"attributes": {"is_volume_muted": not muted}}
            else:
                return
            self.optimistic_until = time.time() + 3
            if not self.demo:
                ok, text = self.sock.cmd(line)
                if not ok:
                    raise RuntimeError(text)
                self.sock.wake.set()
                if line == "play" and last:
                    threading.Thread(target=self._resume_if_silent, args=(last,), daemon=True).start()
        except Exception as ex:
            self.optimistic = {}
            if btn == "play" and not playing and last:
                self.resume(last)
                return
            self.show_toast(tr("failed"))
            log.warning("action %s failed: %s", btn, ex)

    def resume(self, last):
        """Restart the last played station/track through MA (the queue is empty or not playing)."""
        self.optimistic = {"state": "playing"}
        self.optimistic_until = time.time() + 5

        def done(ok):
            if not ok:
                self.optimistic = {}
            self.show_toast(last["name"] if ok else tr("resume_failed"), 2.5)
            self.sock.wake.set()
            self.dirty.set()

        self.ma.play_async(last, done, self.retrying)

    def _resume_if_silent(self, last):
        """'play' on the socket does nothing if MA lost the queue: then start the last played item after all."""
        time.sleep(4)
        st = self.sock.snapshot() or {}
        if (st.get("state") or "").split()[:1] != ["playing"]:
            log.info("play did nothing, resuming %s", last["name"])
            self.resume(last)

    def retrying(self):
        self.show_toast(tr("retrying"), 2.5)
        self.dirty.set()

    def change_member(self, c):
        def done(ok):
            self.show_toast(tr("room_done_add" if c["add"] else "room_done_remove", name=c["name"]) if ok
                            else tr("room_failed"), 2.5)
            self.dirty.set()

        self.ma.set_member_async(c["id"], c["add"], done, self.retrying)

    def run_power(self, kind):
        """Run the configured shutdown/restart command; on failure the player screen comes back."""
        cmd = self.cfg[kind + "_command"]
        if self.demo:
            log.info("demo: would run %s", cmd)
            self.show_toast(tr("power_failed"), 3)
            return
        self.power_busy = kind

        def run():
            try:
                subprocess.run(cmd, check=True, timeout=30, capture_output=True)
                log.info("%s: %s", kind, " ".join(cmd))
            except Exception as ex:
                log.warning("%s failed: %s", kind, getattr(ex, "stderr", None) or ex)
                self.power_busy = None
                self.show_toast(tr("power_failed"), 3)
            self.dirty.set()
        threading.Thread(target=run, daemon=True).start()


__all__ = ["Actions"]
