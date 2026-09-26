"""Blocking IPC client + event subscription for the GTK apps (Hub, Scratchpad)."""

from __future__ import annotations

import json
import socket
import threading
from typing import Any, Callable

from ..config import SOCKET_PATH


class DaemonError(RuntimeError):
    pass


def call(method: str, params: dict | None = None, timeout: float = 10.0) -> Any:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(SOCKET_PATH))
    except OSError as e:
        raise DaemonError("whisprfake-Dienst läuft nicht") from e
    with s:
        s.sendall((json.dumps({"id": 1, "method": method, "params": params or {}}) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    resp = json.loads(buf or b"{}")
    if "error" in resp:
        raise DaemonError(resp["error"])
    return resp.get("result")


def call_async(method: str, params: dict | None, done: Callable[[Any, Exception | None], None],
               timeout: float = 120.0) -> None:
    """Run a call in a thread; `done(result, error)` is invoked on the GTK main loop."""
    from gi.repository import GLib

    def run():
        try:
            res, err = call(method, params, timeout), None
        except Exception as e:
            res, err = None, e
        GLib.idle_add(lambda: (done(res, err), False)[1])

    threading.Thread(target=run, daemon=True).start()


def subscribe(on_event: Callable[[dict], None]) -> None:
    """Background thread delivering daemon events on the GTK main loop; reconnects forever."""
    from gi.repository import GLib

    def run():
        import time

        while True:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(str(SOCKET_PATH))
                s.sendall(b'{"method":"subscribe"}\n')
                f = s.makefile("rb")
                for line in f:
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "event" in ev and ev["event"] != "level":
                        GLib.idle_add(lambda ev=ev: (on_event(ev), False)[1])
            except OSError:
                pass
            GLib.idle_add(lambda: (on_event({"event": "disconnected"}), False)[1])
            time.sleep(2)

    threading.Thread(target=run, daemon=True).start()
