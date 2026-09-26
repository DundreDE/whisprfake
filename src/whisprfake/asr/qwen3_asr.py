"""Qwen3-ASR-1.7B via llama.cpp's llama-server (Vulkan) and its OpenAI-style transcription endpoint."""

from __future__ import annotations

import re
import time

import httpx
import numpy as np

from .base import ASREngine, ASRResult, wav_bytes
from .server_manager import ServerProcess

_TAG = re.compile(r"^\s*language\s+(\w+)\s*<asr_text>", re.I)
_LANGS = {"german": "de", "english": "en"}


class Qwen3ASR(ASREngine):
    name = "qwen3asr"

    def __init__(self, server_bin: str, model: str, mmproj: str, port: int):
        self.server = ServerProcess(
            [server_bin, "-m", model, "--mmproj", mmproj, "--host", "127.0.0.1", "--port", str(port),
             "-ngl", "99", "-c", "4096", "--no-webui"],
            port, health_path="/health", name="llama-server(qwen3-asr)",
        )
        self.client = httpx.AsyncClient(timeout=60.0)

    async def start(self) -> None:
        await self.server.start(timeout=180)

    async def stop(self) -> None:
        await self.server.stop()

    async def transcribe(self, pcm: np.ndarray, prompt: str = "", languages: list[str] | None = None) -> ASRResult:
        t0 = time.perf_counter()
        data = {"model": "qwen3-asr", "response_format": "json"}
        if prompt:
            data["prompt"] = prompt
        r = await self.client.post(self.server.base_url + "/v1/audio/transcriptions", data=data,
                                   files={"file": ("audio.wav", wav_bytes(pcm), "audio/wav")})
        r.raise_for_status()
        text = str(r.json().get("text", ""))
        lang = ""
        if m := _TAG.match(text):
            lang = _LANGS.get(m.group(1).lower(), m.group(1).lower())
            text = text[m.end():]
        text = text.replace("<asr_text>", "").strip()
        return ASRResult(text=text, language=lang, seconds=time.perf_counter() - t0)
