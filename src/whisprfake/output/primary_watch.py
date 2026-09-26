"""Tracks when the Wayland PRIMARY selection last changed and in which window, so Command Mode only
uses PRIMARY when it's a fresh selection made in the window that is focused now (PRIMARY itself can be
hours old)."""

from __future__ import annotations

import asyncio
import json
import logging
import time

log = logging.getLogger(__name__)


class PrimaryWatcher:
    def __init__(self):
        self.changed_at = 0.0
        self.window = ""

    async def run(self) -> None:
        while True:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "wl-paste", "--primary", "--watch", "echo", "changed",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                assert proc.stdout
                async for _ in proc.stdout:
                    self.changed_at = time.monotonic()
                    self.window = await _active_address()
            except Exception as e:
                log.debug("primary watch: %s", e)
            await asyncio.sleep(2)

    async def fresh_for(self, max_age_s: float = 180.0) -> bool:
        if time.monotonic() - self.changed_at > max_age_s:
            return False
        return self.window == await _active_address()


async def _active_address() -> str:
    p = await asyncio.create_subprocess_exec("hyprctl", "activewindow", "-j", stdout=asyncio.subprocess.PIPE,
                                             stderr=asyncio.subprocess.DEVNULL)
    out, _ = await p.communicate()
    try:
        return json.loads(out).get("address", "")
    except Exception:
        return ""
