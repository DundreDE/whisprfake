"""Passive evdev keyboard listener (no grab: keys still reach the compositor/apps).
Requires membership in the `input` group."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

import evdev
from evdev import ecodes

log = logging.getLogger(__name__)


def _is_keyboard(dev: evdev.InputDevice) -> bool:
    keys = dev.capabilities().get(ecodes.EV_KEY, [])
    return ecodes.KEY_LEFTCTRL in keys and ecodes.KEY_A in keys and "whisprfake" not in (dev.name or "").lower()


def _name(code: int) -> str:
    n = ecodes.KEY.get(code) or ecodes.BTN.get(code) or f"KEY_{code}"
    return n[0] if isinstance(n, (list, tuple)) else n


class KeyboardListener:
    def __init__(self, on_key: Callable[[str, bool, float], None]):
        self.on_key = on_key
        self.tasks: dict[str, asyncio.Task] = {}

    async def run(self) -> None:
        while True:
            self._scan()
            await asyncio.sleep(3)

    def _scan(self) -> None:
        for path in evdev.list_devices():
            if path in self.tasks and not self.tasks[path].done():
                continue
            try:
                dev = evdev.InputDevice(path)
            except (PermissionError, OSError) as e:
                if isinstance(e, PermissionError) and not self.tasks:
                    log.error("no permission for %s – is the user in the 'input' group (and re-logged in)?", path)
                continue
            if not _is_keyboard(dev):
                dev.close()
                continue
            log.info("listening on %s (%s)", path, dev.name)
            self.tasks[path] = asyncio.create_task(self._read(dev))

    async def _read(self, dev: evdev.InputDevice) -> None:
        try:
            async for ev in dev.async_read_loop():
                if ev.type == ecodes.EV_KEY and ev.value in (0, 1):  # ignore autorepeat (2)
                    self.on_key(_name(ev.code), ev.value == 1, time.monotonic())
        except OSError:
            log.info("device gone: %s", dev.path)
        finally:
            try:
                dev.close()
            except Exception:
                pass
