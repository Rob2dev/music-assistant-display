"""The control socket of sendspin-cli: playback state and commands."""
import socket
import threading


# ---------------------------------------------------------------- sendspin-cli control socket

class PlayerSocket(threading.Thread):
    """Polls 'status' on the sendspin-cli control socket and sends commands."""

    def __init__(self, path, on_change, interval=1.0):
        super().__init__(daemon=True)
        self.path = path
        self.on_change = on_change
        self.interval = interval
        self.lock = threading.Lock()
        self.status = None  # dict, or None if the player is unreachable
        self.wake = threading.Event()

    def cmd(self, line, timeout=3.0):
        """Sends one command; returns (ok, text)."""
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(timeout)
        try:
            s.connect(self.path)
            s.sendall((line + "\n").encode())
            out = b""
            while chunk := s.recv(4096):
                out += chunk
        finally:
            s.close()
        text = out.decode(errors="replace")
        head, _, rest = text.partition("\n")
        return head.strip() == "ok", rest if head.strip() == "ok" else text.strip()

    def snapshot(self):
        with self.lock:
            return self.status

    def run(self):
        while True:
            try:
                ok, text = self.cmd("status")
                st = {}
                if ok:
                    for line in text.splitlines():
                        k, sep, v = line.partition(":")
                        if sep:
                            st[k.strip()] = v.strip()
                else:
                    st = None
            except OSError:
                st = None
            with self.lock:
                changed = st != self.status
                self.status = st
            if changed:
                self.on_change()
            self.wake.wait(self.interval)
            self.wake.clear()


__all__ = [
    "PlayerSocket",
]
