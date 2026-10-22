"""The Music Assistant API: stations, the group of players, the last played station."""
import io
import json
import logging
import os
import threading
import time

from PIL import Image, ImageOps

log = logging.getLogger("ma-display")


# ---------------------------------------------------------------- Music Assistant API (picking stations)

class MAClient:
    """Fetches radio stations and starts them on the player through the MA websocket API."""

    LOGO = 72

    def __init__(self, cfg, on_change):
        self.url = cfg["ma_url"].rstrip("/")
        self.token = cfg["ma_token"]
        self.player_name = cfg["ma_player_name"]
        self.logo_dir = os.path.join(cfg["state_dir"], "logos")
        self.on_change = on_change
        self.lock = threading.Lock()
        self.stations = {"fav": [], "all": []}
        self.loaded_at = 0
        self.loading = False
        self.error = None
        self.player_id = None
        # Last played station/track, so 'play' can resume it after a player restart (empty queue).
        self.last_path = os.path.join(cfg["state_dir"], "last_played.json")
        self.last_logo_path = os.path.join(cfg["state_dir"], "last_logo.png")
        # The group of this player: who is in it and who could be (see refresh_rooms_async).
        self.rooms = {"leader": None, "rows": []}
        self.rooms_loaded_at = 0
        self.rooms_loading = False
        self.rooms_error = None
        self.remembering = False
        try:
            with open(self.last_path) as f:
                self.last = json.load(f)
        except (OSError, ValueError):
            self.last = None
        try:
            # One fixed object: Renderer.background() remembers the cover by identity.
            self.last_logo = Image.open(self.last_logo_path).convert("RGB") if self.last else None
        except OSError:
            self.last_logo = None

    def snapshot(self):
        with self.lock:
            return {"stations": self.stations, "loading": self.loading, "error": self.error,
                    "loaded": self.loaded_at > 0, "rooms": self.rooms, "rooms_loading": self.rooms_loading,
                    "rooms_error": self.rooms_error}

    def last_played(self):
        with self.lock:
            return self.last

    def set_last(self, uri, name, logo=None):
        item = {"uri": uri, "name": name}
        with self.lock:
            if logo is None:
                logo = next((s["logo"] for s in self.stations["all"] if s["uri"] == uri and s["logo"]), None)
            if self.last == item and (logo is None or self.last_logo is not None):
                return
            if self.last != item:
                self.last_logo = None  # never leave the previous station's logo behind
            if logo is not None:
                self.last_logo = logo
            self.last = item
            keep = self.last_logo
        try:
            os.makedirs(os.path.dirname(self.last_path), exist_ok=True)
            tmp = self.last_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(item, f)
            os.replace(tmp, self.last_path)
            if keep is not None:
                tmp = self.last_logo_path + ".tmp"
                keep.save(tmp, "PNG")
                os.replace(tmp, self.last_logo_path)
            elif os.path.exists(self.last_logo_path):
                os.remove(self.last_logo_path)
            log.info("last played: %s (%s)%s", name, uri, "" if keep is not None else " without logo")
        except OSError as e:
            log.warning("saving last played failed: %s", e)

    def last_logo_for(self):
        with self.lock:
            return self.last_logo

    def remember_async(self):
        """Asks MA what the player is playing right now and remembers it."""
        with self.lock:
            if self.remembering:
                return
            self.remembering = True

        def run():
            try:
                ws, call = self._session()
                try:
                    q = call("player_queues/get_active_queue", player_id=self.player_id) or {}
                finally:
                    ws.close()
                cur = q.get("current_item") or {}
                media = cur.get("media_item") or {}
                uri = media.get("uri") or cur.get("uri")
                if uri:
                    self.set_last(uri, media.get("name") or cur.get("name") or uri)
            except Exception as e:
                log.debug("fetching the active queue failed: %s", e)
            finally:
                with self.lock:
                    self.remembering = False
        threading.Thread(target=run, daemon=True).start()

    def refresh_rooms_async(self, max_age=4, force=False):
        with self.lock:
            if self.rooms_loading or (not force and time.time() - self.rooms_loaded_at < max_age):
                return
            self.rooms_loading = True
        threading.Thread(target=self._refresh_rooms, daemon=True).start()

    def _refresh_rooms(self):
        try:
            ws, call = self._session()
            try:
                players = call("players/all") or []
            finally:
                ws.close()
            rooms = self._build_rooms(players)
            with self.lock:
                self.rooms, self.rooms_error = rooms, None
        except Exception as e:
            log.warning("fetching rooms failed: %s", e)
            with self.lock:
                self.rooms_error = "load_failed"
        finally:
            with self.lock:
                self.rooms_loading = False
                self.rooms_loaded_at = time.time()  # also after an error: do not hammer MA
            self.on_change()

    def _build_rooms(self, players):
        """The leader and one row per player that is in its group or could be (displays excluded)."""
        by_id = {p.get("player_id"): p for p in players}
        leader = by_id.get(self.player_id)
        if leader is None:
            return {"leader": None, "rows": []}
        members = [i for i in (leader.get("group_childs") or []) if i != self.player_id]
        rows = []
        for pid in dict.fromkeys(members + list(leader.get("can_group_with") or [])):
            p = by_id.get(pid)
            if p is None or pid == self.player_id or p.get("type") == "display":
                continue
            rows.append({"id": pid, "name": p.get("display_name") or p.get("name") or pid,
                         "member": pid in members, "available": bool(p.get("available"))})
        rows.sort(key=lambda row: (not row["member"], not row["available"]))  # members first, offline last
        return {"leader": {"id": self.player_id, "name": leader.get("display_name") or leader.get("name")},
                "rows": rows}

    def set_member_async(self, player_id, add, on_done, on_retry=None):
        """Add a player to / remove it from this player's group."""
        def run():
            key = "player_ids_to_add" if add else "player_ids_to_remove"
            for attempt in range(self.RETRIES):
                try:
                    ws, call = self._session()
                    try:
                        call("players/cmd/set_members", target_player=self.player_id, **{key: [player_id]})
                    finally:
                        ws.close()
                except Exception as e:
                    log.warning("%s %s failed (attempt %d/%d): %s", "adding" if add else "removing", player_id,
                                attempt + 1, self.RETRIES, e)
                    if attempt < self.RETRIES - 1:
                        if on_retry:
                            on_retry()
                        time.sleep(self.RETRY_WAIT)
                    continue
                log.info("%s %s %s the group", "added" if add else "removed", player_id, "to" if add else "from")
                self.refresh_rooms_async(force=True)
                on_done(True)
                return
            on_done(False)
        threading.Thread(target=run, daemon=True).start()

    def _session(self):
        import itertools
        import websocket
        ws = websocket.create_connection(self.url.replace("http", "ws", 1) + "/ws", timeout=10)
        ws.recv()  # server info
        counter = itertools.count(1)

        def call(cmd, **args):
            mid = str(next(counter))
            ws.send(json.dumps({"message_id": mid, "command": cmd, "args": args}))
            while True:
                r = json.loads(ws.recv())
                if r.get("message_id") == mid:
                    if "error_code" in r:
                        raise RuntimeError(r.get("details") or r["error_code"])
                    return r.get("result")

        try:
            call("auth", token=self.token)
            if not self.player_id:
                for p in call("players/all"):
                    if (p.get("display_name") or p.get("name")) == self.player_name and p.get("type") != "display":
                        self.player_id = p["player_id"]
                        break
                if not self.player_id:
                    raise RuntimeError(f"player {self.player_name} not found in MA")
        except Exception:
            ws.close()
            raise
        return ws, call

    def _logo(self, item):
        imgs = (item.get("metadata") or {}).get("images") or []
        img = next((i for i in imgs if i.get("type") == "thumb"), imgs[0] if imgs else None)
        if not img:
            return None
        key = img.get("proxy_id") or str(abs(hash(img.get("path"))))
        path = os.path.join(self.logo_dir, key + ".png")
        try:
            return Image.open(path).convert("RGB")
        except OSError:
            pass
        import urllib.request
        urls = []
        if img.get("remotely_accessible") and str(img.get("path", "")).startswith("http"):
            urls.append(img["path"])
        if img.get("proxy_id"):
            urls.append(f"{self.url}/imageproxy/{img['proxy_id']}")
        for url in urls:
            try:
                req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.token})
                with urllib.request.urlopen(req, timeout=8) as r:
                    logo = Image.open(io.BytesIO(r.read())).convert("RGB")
                logo = ImageOps.fit(logo, (self.LOGO, self.LOGO), Image.LANCZOS)
                os.makedirs(self.logo_dir, exist_ok=True)
                logo.save(path)
                return logo
            except Exception as e:
                log.debug("logo %s not loaded: %s", url, e)
        return None

    def refresh_async(self, max_age=600, force=False):
        with self.lock:
            if self.loading or (not force and time.time() - self.loaded_at < max_age):
                return
            self.error = None
            self.loading = True
        threading.Thread(target=self._refresh, daemon=True).start()
        self.on_change()

    RETRIES = 3       # attempts per action
    RETRY_WAIT = 2.0  # seconds between attempts

    def _refresh(self):
        try:
            for attempt in range(self.RETRIES):
                try:
                    ws, call = self._session()
                    try:
                        fav = call("music/radios/library_items", favorite=True, limit=100)
                        allr = call("music/radios/library_items", limit=500)
                    finally:
                        ws.close()

                    def conv(items):
                        return [{"name": r["name"], "uri": r["uri"], "logo": self._logo(r)} for r in items]

                    stations = {"fav": conv(fav), "all": conv(allr)}
                    with self.lock:
                        self.stations, self.loaded_at, self.error = stations, time.time(), None
                    log.info("%d stations loaded (%d favourite)", len(stations["all"]), len(stations["fav"]))
                    return
                except Exception as e:
                    log.warning("fetching stations failed (attempt %d/%d): %s", attempt + 1, self.RETRIES, e)
                    if attempt < self.RETRIES - 1:
                        time.sleep(self.RETRY_WAIT)
            with self.lock:
                self.error = "load_failed"  # translated where it is drawn
        finally:
            with self.lock:
                self.loading = False
            self.on_change()

    def play_async(self, station, on_done, on_retry=None):
        def run():
            for attempt in range(self.RETRIES):
                try:
                    ws, call = self._session()
                    try:
                        call("player_queues/play_media", queue_id=self.player_id, media=station["uri"])
                    finally:
                        ws.close()
                except Exception as e:
                    log.warning("playing %s failed (attempt %d/%d): %s", station["name"], attempt + 1,
                                self.RETRIES, e)
                    if attempt < self.RETRIES - 1:
                        if on_retry:
                            on_retry()
                        time.sleep(self.RETRY_WAIT)
                    continue
                self.set_last(station["uri"], station["name"], station.get("logo"))
                on_done(True)
                return
            on_done(False)
        threading.Thread(target=run, daemon=True).start()


__all__ = [
    "MAClient",
]
