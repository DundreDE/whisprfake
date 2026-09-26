"""Newline-delimited JSON-RPC over a Unix socket + event broadcast to subscribers.

Request:  {"id": 1, "method": "status", "params": {}}
Response: {"id": 1, "result": ...} | {"id": 1, "error": "..."}
Subscribe: {"method": "subscribe"} → the connection then also receives {"event": "...", ...} lines.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

Handler = Callable[[dict], Awaitable[Any]]


class IPCServer:
    def __init__(self, path: Path):
        self.path = path
        self.handlers: dict[str, Handler] = {}
        self.subscribers: set[asyncio.StreamWriter] = set()
        self.server: asyncio.base_events.Server | None = None
        self.snapshot: Callable[[], list[dict]] | None = None  # events sent on subscribe

    def method(self, name: str):
        def deco(fn: Handler) -> Handler:
            self.handlers[name] = fn
            return fn
        return deco

    async def start(self) -> None:
        if self.path.exists():
            self.path.unlink()
        self.server = await asyncio.start_unix_server(self._client, path=str(self.path))
        os.chmod(self.path, 0o600)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while line := await reader.readline():
                try:
                    req = json.loads(line)
                except json.JSONDecodeError:
                    continue
                method = req.get("method", "")
                if method == "subscribe":
                    self.subscribers.add(writer)
                    for ev in (self.snapshot() if self.snapshot else []):
                        self._write(writer, ev)
                    continue
                resp: dict = {"id": req.get("id")}
                fn = self.handlers.get(method)
                if fn is None:
                    resp["error"] = f"unknown method {method}"
                else:
                    try:
                        resp["result"] = await fn(req.get("params") or {})
                    except Exception as e:
                        log.exception("ipc %s failed", method)
                        resp["error"] = str(e)
                self._write(writer, resp)
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            self.subscribers.discard(writer)
            writer.close()

    def _write(self, w: asyncio.StreamWriter, obj: dict) -> None:
        try:
            w.write((json.dumps(obj, ensure_ascii=False, default=str) + "\n").encode())
        except Exception:
            self.subscribers.discard(w)

    def emit(self, event: str, **data) -> None:
        msg = {"event": event, **data}
        for w in list(self.subscribers):
            if w.is_closing():
                self.subscribers.discard(w)
                continue
            self._write(w, msg)


async def call(path: Path, method: str, params: dict | None = None, timeout: float = 30.0) -> Any:
    reader, writer = await asyncio.open_unix_connection(str(path))
    writer.write((json.dumps({"id": 1, "method": method, "params": params or {}}) + "\n").encode())
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout)
    writer.close()
    resp = json.loads(line)
    if "error" in resp:
        raise RuntimeError(resp["error"])
    return resp.get("result")
