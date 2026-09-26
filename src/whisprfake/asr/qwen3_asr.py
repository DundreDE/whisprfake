"""Qwen3-ASR-1.7B via llama.cpp's llama-server (Vulkan), driven through the chat API so we can use the
model's two strengths:

* **context biasing** – the system message carries your dictionary (and names on screen). Qwen3-ASR was
  trained to use it: "Medikinet", "TONOR", "PostgreSQL", "Ingelheim" come out right.
* **language forcing** – the assistant turn can be prefilled with "language German<asr_text>".

Strategy: let the model detect the language; if it picks something outside your languages (happens on very
short or quiet clips) decode again forced to your primary language. Very low confidence = noise → empty.
"""

from __future__ import annotations

import asyncio
import base64
import re
import time

import httpx
import numpy as np

from .base import ASREngine, ASRResult, wav_bytes
from .server_manager import ServerProcess

_TAG = re.compile(r"^\s*language\s+([A-Za-z]+)\s*<asr_text>", re.I)
LANG_NAMES = {"de": "German", "en": "English", "fr": "French", "es": "Spanish", "it": "Italian", "nl": "Dutch",
              "pl": "Polish", "pt": "Portuguese", "ru": "Russian", "tr": "Turkish"}
NAME_CODES = {v.lower(): k for k, v in LANG_NAMES.items()}
NOISE_LOGPROB = -0.7


class Qwen3ASR(ASREngine):
    name = "qwen3asr"

    def __init__(self, server_bin: str, model: str, mmproj: str, port: int):
        self.server = ServerProcess(
            [server_bin, "-m", model, "--mmproj", mmproj, "--host", "127.0.0.1", "--port", str(port),
             "-ngl", "99", "-c", "8192", "-np", "2", "--no-webui", "--cache-reuse", "256"],
            port, health_path="/health", name="llama-server(qwen3-asr)",
        )
        self.client = httpx.AsyncClient(timeout=120.0)

    async def start(self) -> None:
        await self.server.start(timeout=180)

    async def stop(self) -> None:
        await self.server.stop()

    async def _decode(self, b64: str, context: str, force: str | None, max_tokens: int) -> tuple[str, str, float]:
        msgs = [{"role": "system", "content": context},
                {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}}]}]
        if force:
            msgs.append({"role": "assistant", "content": f"language {force}<asr_text>"})
        r = await self.client.post(self.server.base_url + "/v1/chat/completions",
                                   json={"messages": msgs, "temperature": 0, "max_tokens": max_tokens, "logprobs": True})
        r.raise_for_status()
        ch = r.json()["choices"][0]
        text = ch["message"]["content"] or ""
        lps = [t["logprob"] for t in (ch.get("logprobs") or {}).get("content", []) if "logprob" in t]
        lang = force or ""
        if m := _TAG.match(text):
            lang, text = m.group(1), text[m.end():]
        text = text.replace("<asr_text>", "").strip()
        return text, lang, (sum(lps) / len(lps)) if lps else 0.0

    async def transcribe(self, pcm: np.ndarray, prompt: str = "", languages: list[str] | None = None) -> ASRResult:
        t0 = time.perf_counter()
        langs = languages or ["de", "en"]
        allowed = {LANG_NAMES.get(x, x).lower() for x in langs}
        b64 = base64.b64encode(wav_bytes(pcm)).decode()
        max_tokens = int(len(pcm) / 16000 * 8) + 32  # ~8 tokens/s of speech is generous
        context = prompt or ""
        if len(langs) == 1:
            text, lang, lp = await self._decode(b64, context, LANG_NAMES.get(langs[0], langs[0]), max_tokens)
        else:
            text, lang, lp = await self._decode(b64, context, None, max_tokens)
            if lang.lower() not in allowed:
                text, lang, lp = await self._decode(b64, context, LANG_NAMES.get(langs[0], langs[0]), max_tokens)
        if lp < NOISE_LOGPROB and len(text.split()) <= 4:
            text = ""  # the model is guessing at noise
        return ASRResult(text=text, language=NAME_CODES.get(lang.lower(), lang.lower()),
                         seconds=time.perf_counter() - t0)

    async def warm(self) -> None:
        await asyncio.gather(*(self.transcribe(np.zeros(16000, np.float32)) for _ in range(2)))
