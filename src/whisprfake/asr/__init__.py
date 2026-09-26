from __future__ import annotations

import os

from ..config import Config
from .base import ASREngine, ASRResult


def make_engine(cfg: Config, engine: str | None = None) -> ASREngine:
    engine = engine or cfg.asr.engine
    threads = max(4, (os.cpu_count() or 8) // 2)
    if engine == "whisper":
        from .whisper_cpp import WhisperCpp

        return WhisperCpp(cfg.asr.whisper_server_bin, str(cfg.model_path(cfg.asr.whisper_model)), cfg.asr.port, threads)
    if engine == "qwen3asr":
        from .qwen3_asr import Qwen3ASR

        return Qwen3ASR(cfg.asr.llama_server_bin, str(cfg.model_path(cfg.asr.qwen3asr_model)),
                        str(cfg.model_path(cfg.asr.qwen3asr_mmproj)), cfg.asr.port + 1)
    if engine == "parakeet_onnx":
        from .parakeet_onnx import ParakeetOnnx

        return ParakeetOnnx(threads)
    if engine == "parakeet":
        from .parakeet_cpp import ParakeetCpp

        return ParakeetCpp(cfg)
    raise ValueError(f"unknown ASR engine {engine}")


__all__ = ["ASREngine", "ASRResult", "make_engine"]
