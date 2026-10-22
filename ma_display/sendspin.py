"""Our own Sendspin client: metadata, artwork, controller state and the spectrum."""
import asyncio
import contextlib
import io
import json
import logging
import os
import threading
import time

from PIL import Image

log = logging.getLogger("ma-display")


# ---------------------------------------------------------------- Sendspin display client

class SendspinDisplay(threading.Thread):
    """Sendspin client with metadata/artwork/controller roles, in its own asyncio thread."""

    def __init__(self, cfg, on_change):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.on_change = on_change
        self.lock = threading.Lock()
        self.loop = None
        self.client = None
        self.connected = False
        self.meta = {}  # title, artist, album, duration_ms, progress_ms, speed, at
        self.cover = None
        self.group_state = None
        self.volume = None
        self.muted = None
        self.pin = None
        self.viz_enabled = True  # False: drop the spectrum frames (analyser switched off)
        from collections import deque
        self.viz = deque(maxlen=VIZ_FPS * 15)  # (playback moment µs, spectrum); ~8 s ahead

    def snapshot(self):
        with self.lock:
            return {"connected": self.connected, "meta": dict(self.meta), "cover": self.cover,
                    "group_state": self.group_state, "volume": self.volume,
                    "muted": self.muted, "pin": self.pin}

    def switch(self):
        """Ask the server to move this display to the next playing group."""
        from aiosendspin.models.types import MediaCommand
        client, loop = self.client, self.loop
        if client is not None and loop is not None:
            fut = asyncio.run_coroutine_threadsafe(client.send_group_command(MediaCommand.SWITCH), loop)
            fut.add_done_callback(lambda f: f.exception() and log.warning("switch failed: %s", f.exception()))

    def run(self):
        try:
            asyncio.run(self._main())
        except Exception:
            log.exception("Sendspin client stopped")

    def _identity(self):
        from aiosendspin.noise import Identity, b64url_decode, b64url_encode
        os.makedirs(self.cfg["state_dir"], exist_ok=True)
        path = os.path.join(self.cfg["state_dir"], "identity.json")
        try:
            with open(path) as f:
                return Identity.from_private_bytes(b64url_decode(json.load(f)["private_key"]))
        except FileNotFoundError:
            ident = Identity.generate()
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"private_key": b64url_encode(ident.private_bytes)}, f)
            return ident

    async def _main(self):
        from aiosendspin.client import ClientListener
        from aiosendspin.noise import FileClientPairingStore
        self.loop = asyncio.get_running_loop()
        self.identity = self._identity()
        self.store = await FileClientPairingStore.open(os.path.join(self.cfg["state_dir"], "pairing-store.json"))
        # Offer guest access: MA approves such a client automatically (like sendspin-cli);
        # without it the roles stay inactive until someone pairs it in MA.
        pcfg = await self.store.get_pairing_config()
        if not pcfg.unpaired_access_enabled:
            import dataclasses
            await self.store.store_pairing_config(dataclasses.replace(pcfg, unpaired_access_enabled=True))
            log.info("unpaired (guest) access enabled")
        listener = ClientListener(client_id=self.identity.peer_id, on_connection=self._on_connection,
                                  port=self.cfg["sendspin_port"], client_name=self.cfg["sendspin_name"])
        await listener.start()
        log.info("Sendspin display listening on port %d (id %s)", self.cfg["sendspin_port"], self.identity.peer_id)
        await asyncio.Event().wait()

    async def _show_pin(self, code):
        with self.lock:
            self.pin = code
        self.on_change()

    async def _on_connection(self, ws):
        from aiosendspin.client import PairingSupport, SendspinClient
        from aiosendspin.models.artwork import ArtworkChannel, ClientHelloArtworkSupport
        from aiosendspin.models.types import ArtworkSource, PictureFormat, Roles
        from aiosendspin.models.visualizer import ClientHelloVisualizerSpectrum, ClientHelloVisualizerSupport
        log.info("Music Assistant connecting")
        client = SendspinClient(
            identity=self.identity, client_name=self.cfg["sendspin_name"],
            roles=[Roles.METADATA, Roles.ARTWORK, Roles.CONTROLLER, Roles.VISUALIZER],
            pairing_store=self.store,
            pairing_support=PairingSupport(pin_display=self._show_pin, offer_static_pin=False),
            artwork_support=ClientHelloArtworkSupport(channels=[ArtworkChannel(
                source=ArtworkSource.ALBUM, format=PictureFormat.JPEG, media_width=184, media_height=184)]),
            # The server analyses the audio; we receive ready-made spectrum bands and beats.
            visualizer_support=ClientHelloVisualizerSupport(
                buffer_capacity=65536, rate_max=VIZ_FPS, types=["spectrum", "beat"],
                # mel instead of log: with log the lowest bands are narrower than the server's
                # FFT resolution and would always stay empty.
                spectrum=ClientHelloVisualizerSpectrum(n_disp_bins=VIZ_BINS, scale="mel", f_min=40, f_max=16000)),
        )
        client.add_metadata_listener(self._on_metadata)
        client.add_controller_state_listener(self._on_controller)
        client.add_group_update_listener(self._on_group)
        client.add_artwork_listener(self._on_artwork)
        client.add_visualizer_listener(lambda frames: self._on_visualizer(client, frames))
        # On stop/seek whatever was buffered ahead is no longer valid.
        client.add_stream_clear_listener(self._clear_viz)
        client.add_stream_end_listener(self._clear_viz)
        task = asyncio.create_task(client.attach_websocket(ws))
        try:
            async with asyncio.timeout(30):
                while not client.connected and not task.done():
                    await asyncio.sleep(0.05)
        except TimeoutError:
            task.cancel()
        if not client.connected:
            log.warning("Handshake with server failed")
            with contextlib.suppress(BaseException):
                await task
            return
        log.info("Connected to %s", client.server_info.name if client.server_info else "?")
        with self.lock:
            self.client, self.connected, self.pin = client, True, None
        self.on_change()
        try:
            with contextlib.suppress(Exception):
                await task
        finally:
            with self.lock:
                if self.client is client:
                    self.client, self.connected = None, False
                    self.meta, self.cover, self.group_state = {}, None, None
            log.info("Connection to server closed")
            self.on_change()

    def _on_metadata(self, payload):
        from aiosendspin.models.types import UndefinedField
        md = payload.metadata
        if isinstance(md, UndefinedField):
            return
        with self.lock:
            if md is None:
                self.meta = {}
                self.cover = None
            else:
                # Missing fields (UndefinedField) stay unchanged; None clears.
                for key in ("title", "artist", "album"):
                    v = getattr(md, key)
                    if not isinstance(v, UndefinedField):
                        self.meta[key] = v
                p = md.progress
                if not isinstance(p, UndefinedField):
                    if p is None:
                        for k in ("duration_ms", "progress_ms", "speed", "at"):
                            self.meta.pop(k, None)
                    else:
                        self.meta.update(duration_ms=p.track_duration, progress_ms=p.track_progress,
                                         speed=p.playback_speed, at=time.time())
        self.on_change()

    def _on_controller(self, payload):
        from aiosendspin.models.types import UndefinedField
        c = payload.controller
        if c is None or isinstance(c, UndefinedField):
            return
        with self.lock:
            self.volume, self.muted = c.volume, c.muted
        self.on_change()

    def _on_group(self, payload):
        if payload.playback_state is not None:
            with self.lock:
                self.group_state = payload.playback_state.value
            self.on_change()

    def _on_visualizer(self, client, frames):
        """Buffer frames keyed by the moment the sound is audible here (local clock, µs).

        MA sends them ~8 s ahead; current_spectrum() picks the frame that is sounding now.
        """
        if not self.viz_enabled:
            return
        with self.lock:
            for f in frames:
                if f.spectrum is not None:
                    self.viz.append((client.compute_play_time(f.timestamp_us), f.spectrum))
                    if os.environ.get("MA_DISPLAY_VIZDEBUG") and len(self.viz) % 20 == 0:
                        log.info("spectrum %s", f.spectrum)

    def _clear_viz(self, roles=None):
        if roles is None or "visualizer" in roles:
            with self.lock:
                self.viz.clear()

    def current_spectrum(self):
        """The spectrum that is audible now, or None if there is nothing (for a moment)."""
        client = self.client
        if client is None:
            return None
        now = client.now_us()
        with self.lock:
            current = None
            while self.viz and self.viz[0][0] <= now:
                current = self.viz.popleft()
            if current is not None:
                self._viz_last = current
            last = getattr(self, "_viz_last", None)
        if last is None or now - last[0] > 500_000:  # nothing for over 0.5 s: silence
            return None
        return last[1]

    def _on_artwork(self, channel, data):
        if channel != 0:
            return
        img = None
        if data:
            try:
                img = Image.open(io.BytesIO(data)).convert("RGB")
            except OSError as e:
                log.warning("artwork could not be decoded: %s", e)
        with self.lock:
            self.cover = img
        self.on_change()


VIZ_FPS = 20   # max. visualizer frames per second we ask the server for


VIZ_BINS = 24  # number of spectrum bands


__all__ = [
    "SendspinDisplay", "VIZ_FPS", "VIZ_BINS",
]
