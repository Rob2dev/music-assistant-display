"""Merging the player socket, Sendspin and Music Assistant into one state."""
import copy
import logging
import time

from ..demo import DEMO_STATE, _iso_at
from ..i18n import tr

log = logging.getLogger("ma-display")


def _blank(v):
    return v if v and v.lower() not in ("none", "-", "unknown", "(none)") else None


def same_track(meta, sock_title):
    """Does the Sendspin metadata belong to what sendspin-cli is playing? It sometimes reports 'ARTIST - Title'."""
    title = (meta.get("title") or "").casefold().strip()
    s = (sock_title or "").casefold().strip()
    if not title:
        return False
    return not s or s == title or s.endswith(" - " + title) or title in s


class PlayerState:
    # Seconds the player plays while the display shows something else, before it sends 'switch'.
    SWITCH_AFTER = 6
    # The service starts before sendspin-cli is up. For this long an unreachable player reads
    # as "Starting…" rather than as an error.
    STARTUP_SECS = 90

    # -- merge state into a single dict (same shape as an HA media_player)
    def snapshot(self):
        """Returns (data, error label, cover)."""
        if self.demo:
            return copy.deepcopy(DEMO_STATE), None, self._demo_cover
        st = self.sock.snapshot()
        ss = self.ss.snapshot()
        if st is None:
            if time.time() - self.started < self.STARTUP_SECS:
                return {"state": "starting", "attributes": {}}, None, None
            return {"state": "unavailable", "attributes": {}}, tr("err_no_player"), None
        state = (st.get("state") or "idle").split()[0].lower()
        sock_title = _blank(st.get("track"))
        m = ss["meta"]
        rich = ss["connected"] and same_track(m, sock_title)
        a = {}
        if rich:
            a["media_title"] = m.get("title")
            a["media_artist"] = m.get("artist")
            a["media_album_name"] = m.get("album")
            if "duration_ms" in m:
                a["media_duration"] = m["duration_ms"] / 1000
                a["media_position"] = m["progress_ms"] / 1000
                a["media_position_updated_at"] = _iso_at(m["at"])
            cover = ss["cover"]
        else:
            a["media_title"] = sock_title
            cover = None
            # No Sendspin metadata yet (just started): for radio, show the remembered station name already.
            last = self.ma.last_played() if self.ma else None
            if sock_title and state == "playing" and last and "/radio/" in last["uri"]:
                a["media_album_name"] = last["name"]
        try:
            a["volume_level"] = int(st.get("group volume", "").split()[0]) / 100
        except (ValueError, IndexError):
            pass
        if ss["muted"] is not None and rich:
            a["is_volume_muted"] = ss["muted"]
        last = self.ma.last_played() if self.ma else None
        if last and not a.get("media_title"):
            a["resume_name"] = last["name"]
        if cover is None and last and "/radio/" in last["uri"]:
            # Station logo as cover until the real artwork arrives (or when the queue is empty).
            if not a.get("media_title") or a.get("media_album_name") == last["name"]:
                cover = self.ma.last_logo_for()
        data = {"state": state, "attributes": a}
        if self.optimistic and time.time() < self.optimistic_until:
            if "state" in self.optimistic:
                data["state"] = self.optimistic["state"]
            a.update(self.optimistic.get("attributes", {}))
        error = None
        if "connected" not in (st.get("server") or ""):
            error = tr("err_no_ma")
        if ss["pin"]:
            error = tr("err_pairing", pin=ss["pin"])
        self._maybe_switch(state, sock_title, ss)
        return data, error, cover

    def _maybe_switch(self, state, sock_title, ss):
        """Send the display to the player's group if it shows something else."""
        now = time.time()
        if not ss["connected"] or state != "playing":
            self.mismatch_since = None
            return
        mismatch = ss["group_state"] != "playing" or not same_track(ss["meta"], sock_title)
        if not mismatch:
            self.mismatch_since = None
            return
        self.mismatch_since = self.mismatch_since or now
        if now - self.mismatch_since >= self.SWITCH_AFTER and now - self.last_switch >= self.SWITCH_AFTER:
            log.info("display is not showing what the player plays (%r vs %r): switch",
                     ss["meta"].get("title"), sock_title)
            self.last_switch = now
            self.ss.switch()


__all__ = ["PlayerState", "_blank", "same_track"]
