"""Minimal async Ollama chat client (local only)."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx


@dataclass
class LLMResult:
    text: str
    seconds: float
    prompt_tokens: int = 0
    output_tokens: int = 0


class Ollama:
    def __init__(self, base_url: str, keep_alive: str = "-1", timeout: float = 8.0):
        self.base_url = base_url.rstrip("/")
        self.keep_alive = int(keep_alive) if keep_alive.lstrip("-").isdigit() else keep_alive
        self.client = httpx.AsyncClient(timeout=timeout)

    async def chat(self, model: str, messages: list[dict], *, temperature: float = 0.0,
                   max_tokens: int | None = None, timeout: float | None = None) -> LLMResult:
        t0 = time.perf_counter()
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": temperature, "top_p": 0.9, "num_ctx": 8192,
                        **({"num_predict": max_tokens} if max_tokens else {})},
        }
        r = await self.client.post(self.base_url + "/api/chat", json=body,
                                   timeout=timeout or self.client.timeout)
        if r.status_code == 400 and "think" in r.text:
            body.pop("think")  # model without thinking support
            r = await self.client.post(self.base_url + "/api/chat", json=body)
        r.raise_for_status()
        j = r.json()
        return LLMResult(
            text=j["message"]["content"],
            seconds=time.perf_counter() - t0,
            prompt_tokens=j.get("prompt_eval_count", 0),
            output_tokens=j.get("eval_count", 0),
        )

    async def warm(self, model: str) -> None:
        """Load the model into VRAM and pin it there (keep_alive=-1)."""
        await self.client.post(self.base_url + "/api/generate",
                               json={"model": model, "prompt": "", "keep_alive": self.keep_alive}, timeout=120)
