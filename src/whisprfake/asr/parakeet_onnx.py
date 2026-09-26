"""Parakeet TDT 0.6B v3 on CPU via onnx-asr (25 European languages, automatic language ID)."""

from __future__ import annotations

import asyncio
import time

import numpy as np

from .base import ASREngine, ASRResult


class ParakeetOnnx(ASREngine):
    name = "parakeet_onnx"

    def __init__(self, threads: int = 8):
        self.threads = threads
        self.model = None

    async def start(self) -> None:
        if self.model is None:
            self.model = await asyncio.to_thread(self._load)

    def _load(self):
        import onnx_asr
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = self.threads
        return onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", sess_options=so)

    async def transcribe(self, pcm: np.ndarray, prompt: str = "", languages: list[str] | None = None) -> ASRResult:
        await self.start()
        t0 = time.perf_counter()
        text = await asyncio.to_thread(self.model.recognize, pcm.astype(np.float32), sample_rate=16000)
        return ASRResult(text=str(text).strip(), seconds=time.perf_counter() - t0)
