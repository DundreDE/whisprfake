"""Whisper large-v3-turbo via whisper.cpp's whisper-server (Vulkan build)."""

from __future__ import annotations

import time

import httpx
import numpy as np

from .base import ASREngine, ASRResult, wav_bytes
from .server_manager import ServerProcess

# whisper.cpp reports full language names in verbose_json
_LANG_CODES = {"german": "de", "english": "en", "french": "fr", "spanish": "es", "italian": "it", "dutch": "nl"}


class WhisperCpp(ASREngine):
    name = "whisper"

    def __init__(self, server_bin: str, model: str, port: int, threads: int = 8):
        self.server = ServerProcess(
            [server_bin, "-m", model, "--host", "127.0.0.1", "--port", str(port), "-t", str(threads),
             "--no-timestamps", "-l", "auto"],
            port, name="whisper-server",
        )
        self.client = httpx.AsyncClient(timeout=60.0)

    async def start(self) -> None:
        await self.server.start()

    async def stop(self) -> None:
        await self.server.stop()

    async def _infer(self, audio: bytes, language: str, prompt: str) -> dict:
        data = {"response_format": "verbose_json", "temperature": "0.0", "language": language, "no_timestamps": "true"}
        if prompt:
            data["prompt"] = prompt
        r = await self.client.post(self.server.base_url + "/inference", data=data,
                                   files={"file": ("audio.wav", audio, "audio/wav")})
        r.raise_for_status()
        return r.json()

    async def transcribe(self, pcm: np.ndarray, prompt: str = "", languages: list[str] | None = None) -> ASRResult:
        t0 = time.perf_counter()
        audio = wav_bytes(pcm)
        langs = languages or []
        lang = langs[0] if len(langs) == 1 else "auto"
        j = await self._infer(audio, lang, prompt)
        detected = _LANG_CODES.get(str(j.get("language", "")).lower(), str(j.get("language", "")))
        if lang == "auto" and langs and detected not in langs:
            # Mis-detected (e.g. accented German as Dutch): redo with the first allowed language.
            j = await self._infer(audio, langs[0], prompt)
            detected = langs[0]
        text = str(j.get("text", "")).strip()
        return ASRResult(text=_strip_hallucinations(text), language=detected, seconds=time.perf_counter() - t0)


_HALLUCINATIONS = (
    "untertitel im auftrag des zdf", "untertitel der amara.org-community", "vielen dank fürs zuschauen",
    "thanks for watching", "thank you for watching", "[music]", "[musik]", "(music)", "[blank_audio]", "*musik*",
)


def _strip_hallucinations(text: str) -> str:
    low = text.lower().strip(" .!")
    if any(low == h.strip(" .!") for h in _HALLUCINATIONS):
        return ""
    for h in _HALLUCINATIONS:
        idx = text.lower().find(h)
        if idx >= 0:
            text = (text[:idx] + text[idx + len(h):]).strip()
    return text
