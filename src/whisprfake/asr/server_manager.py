"""Runs a local inference server (whisper-server / llama-server) as a child process."""

from __future__ import annotations

import asyncio
import logging
import os
import signal

import httpx

log = logging.getLogger(__name__)


class ServerProcess:
    def __init__(self, argv: list[str], port: int, health_path: str = "/", name: str = "server"):
        self.argv, self.port, self.health_path, self.name = argv, port, health_path, name
        self.proc: asyncio.subprocess.Process | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self, timeout: float = 120.0) -> None:
        if self.proc and self.proc.returncode is None:
            return
        log.info("starting %s: %s", self.name, " ".join(self.argv))
        self.proc = await asyncio.create_subprocess_exec(
            *self.argv,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        asyncio.create_task(self._drain())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        async with httpx.AsyncClient(timeout=2.0) as c:
            while loop.time() < deadline:
                if self.proc.returncode is not None:
                    raise RuntimeError(f"{self.name} exited with {self.proc.returncode}")
                try:
                    r = await c.get(self.base_url + self.health_path)
                    if r.status_code < 500:
                        log.info("%s ready on :%d", self.name, self.port)
                        return
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.25)
        raise TimeoutError(f"{self.name} did not become ready")

    async def _drain(self) -> None:
        assert self.proc and self.proc.stderr
        async for line in self.proc.stderr:
            log.debug("[%s] %s", self.name, line.decode(errors="replace").rstrip())

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except TimeoutError:
                os.killpg(self.proc.pid, signal.SIGKILL)
        self.proc = None
