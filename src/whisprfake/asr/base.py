from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
import soundfile as sf


@dataclass
class ASRResult:
    text: str
    language: str = ""
    seconds: float = 0.0  # processing time


class ASREngine:
    name = "base"

    async def start(self) -> None:  # load model / spawn server
        pass

    async def stop(self) -> None:
        pass

    async def transcribe(self, pcm: np.ndarray, prompt: str = "", languages: list[str] | None = None) -> ASRResult:
        raise NotImplementedError


def wav_bytes(pcm: np.ndarray, sr: int = 16000) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, pcm.astype(np.float32), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()
