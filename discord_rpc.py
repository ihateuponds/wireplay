"""Discord Rich Presence over the local IPC socket. Zero dependencies, never blocks the caller.

Protocol: a unix stream socket at $XDG_RUNTIME_DIR/discord-ipc-N. Every frame is
<int32 opcode><int32 length><JSON>, little-endian. Opcode 0 = handshake, 1 = frame, 2 = close.

All socket work happens on one daemon thread fed by a queue, so a missing Discord, a slow socket or a
protocol error can only ever make a status update silently not happen.
"""
import json
import os
import queue
import socket
import struct
import threading
import time
import uuid
from pathlib import Path

import config

DEFAULT_CLIENT_ID = "1348480356501164082"
OP_HANDSHAKE, OP_FRAME, OP_CLOSE = 0, 1, 2
CONNECT_TIMEOUT = 1.5
RETRY_AFTER = 10.0                        # seconds to wait before trying to reach Discord again after a failure


def socket_candidates():
    """Likely IPC socket paths, most probable first (native, /run/user fallback, Flatpak, Snap)."""
    bases = []
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        bases.append(Path(runtime))
    bases.append(Path(f"/run/user/{os.getuid()}"))
    out = []
    for base in dict.fromkeys(bases):
        for sub in (".", "app/com.discordapp.Discord", "app/dev.vencord.Vesktop", "snap.discord"):
            for n in range(10):
                out.append(base / sub / f"discord-ipc-{n}")
    return out


def encode(opcode: int, payload: dict) -> bytes:
    body = json.dumps(payload, separators=(",", ":")).encode()
    return struct.pack("<ii", opcode, len(body)) + body


def _read_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Discord closed the connection")
        buf += chunk
    return buf


def read_frame(sock):
    opcode, length = struct.unpack("<ii", _read_exact(sock, 8))
    if not 0 <= length <= 1 << 20:
        raise ValueError("implausible frame length")
    return opcode, json.loads(_read_exact(sock, length) or b"{}")


class Client:
    """Synchronous IPC client (used by Presence's worker thread; usable directly in tests)."""

    def __init__(self, client_id):
        self.client_id, self.sock = client_id, None

    def connect(self):
        for path in socket_candidates():
            if not path.exists():
                continue
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(CONNECT_TIMEOUT)
            try:
                s.connect(str(path))
                s.sendall(encode(OP_HANDSHAKE, {"v": 1, "client_id": self.client_id}))
                opcode, reply = read_frame(s)             # READY, or an error / close frame
                if opcode == OP_FRAME and reply.get("evt") == "READY":
                    self.sock = s
                    return True
            except (OSError, ValueError):
                pass
            s.close()
        return False

    def set_activity(self, activity):
        """activity=None clears the status. Returns True when Discord acknowledged it."""
        args = {"pid": os.getpid()}
        if activity is not None:
            args["activity"] = activity
        self.sock.sendall(encode(OP_FRAME, {"cmd": "SET_ACTIVITY", "args": args, "nonce": str(uuid.uuid4())}))
        opcode, reply = read_frame(self.sock)
        return opcode == OP_FRAME and reply.get("evt") != "ERROR"

    def close(self):
        if self.sock:
            try:
                self.sock.sendall(encode(OP_CLOSE, {}))
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


class Presence:
    """Fire-and-forget Rich Presence. set()/clear()/shutdown() return immediately and never raise."""

    def __init__(self, client_id=None, enabled=None):
        cfg = config.load()
        self.client_id = str(client_id or cfg.get("discord_client_id") or DEFAULT_CLIENT_ID)
        self.enabled = bool(cfg.get("discord_rpc", True)) if enabled is None else enabled
        self._q = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()

    def set_enabled(self, on: bool):
        """Turn Rich Presence on or off while running (Settings). Turning it off clears a status that is showing."""
        on = bool(on)
        if self.enabled and not on:
            self.clear()
        self.enabled = on

    def set(self, title, platform_label, start_epoch=None):
        self._send(("set", {
            "details": title[:128] or "Playing",
            "state": platform_label[:128] or "wireplay",
            "timestamps": {"start": int(start_epoch or time.time())},
        }))

    def clear(self):
        self._send(("clear", None))

    def shutdown(self, timeout=1.5):
        """Clear the status, close the socket and stop the worker (waits at most `timeout`)."""
        if self._thread is None:
            return
        self._send(("quit", None))
        self._thread.join(timeout)

    def _send(self, item):
        if not self.enabled:
            return
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True, name="discord-rpc")
                self._thread.start()
        self._q.put(item)

    def _run(self):
        client, retry_at, current = Client(self.client_id), 0.0, None
        while True:
            cmd, activity = self._q.get()
            if cmd == "set":
                current = activity
            elif cmd == "clear":
                current = None
            try:
                if cmd == "quit":
                    if client.sock:
                        client.set_activity(None)
                    return
                if client.sock is None:
                    if time.monotonic() < retry_at or not client.connect():
                        retry_at = time.monotonic() + RETRY_AFTER
                        continue
                client.set_activity(current)
            except (OSError, ValueError):                  # Discord went away mid-conversation: reconnect next time
                client.close()
                retry_at = time.monotonic() + RETRY_AFTER
            finally:
                if cmd == "quit":
                    client.close()
