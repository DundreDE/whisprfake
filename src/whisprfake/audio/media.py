"""Pause MPRIS media players while dictating and resume the ones we paused afterwards."""

from __future__ import annotations

import logging

from dbus_next import BusType
from dbus_next.aio import MessageBus

log = logging.getLogger(__name__)
PREFIX = "org.mpris.MediaPlayer2."


class MediaPauser:
    def __init__(self):
        self.bus: MessageBus | None = None
        self.paused: list[str] = []

    async def _bus(self) -> MessageBus:
        if self.bus is None or not self.bus.connected:
            self.bus = await MessageBus(bus_type=BusType.SESSION).connect()
        return self.bus

    async def _player(self, name: str):
        bus = await self._bus()
        intro = await bus.introspect(name, "/org/mpris/MediaPlayer2")
        obj = bus.get_proxy_object(name, "/org/mpris/MediaPlayer2", intro)
        return obj.get_interface("org.mpris.MediaPlayer2.Player")

    async def pause_all(self) -> None:
        try:
            bus = await self._bus()
            dbus = bus.get_proxy_object("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                        await bus.introspect("org.freedesktop.DBus", "/org/freedesktop/DBus"))
            names = await dbus.get_interface("org.freedesktop.DBus").call_list_names()
            for n in names:
                if not n.startswith(PREFIX):
                    continue
                try:
                    p = await self._player(n)
                    if await p.get_playback_status() == "Playing":
                        await p.call_pause()
                        self.paused.append(n)
                except Exception as e:
                    log.debug("mpris %s: %s", n, e)
        except Exception as e:
            log.debug("mpris pause failed: %s", e)

    async def resume(self) -> None:
        names, self.paused = self.paused, []
        for n in names:
            try:
                await (await self._player(n)).call_play()
            except Exception as e:
                log.debug("mpris resume %s: %s", n, e)
