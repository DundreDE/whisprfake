"""Parakeet TDT 0.6B v3 on the GPU through whisper.cpp's libparakeet (Vulkan), bound with ctypes.
Model stays resident; 25 European languages with automatic language ID; no prompt biasing."""

from __future__ import annotations

import asyncio
import ctypes as C
import threading
import time
from pathlib import Path

import numpy as np

from .base import ASREngine, ASRResult


class _CtxParams(C.Structure):
    _fields_ = [("use_gpu", C.c_bool), ("gpu_device", C.c_int)]


class _FullParams(C.Structure):
    _fields_ = [("strategy", C.c_int), ("n_threads", C.c_int), ("offset_ms", C.c_int), ("duration_ms", C.c_int),
                ("no_context", C.c_bool), ("audio_ctx", C.c_int)] + [(f"p{i}", C.c_void_p) for i in range(10)]


class ParakeetCpp(ASREngine):
    name = "parakeet"

    def __init__(self, cfg):
        self.lib_path = Path(cfg.asr.whisper_server_bin).parent / "libparakeet.so"
        self.model = str(cfg.model_path(cfg.asr.parakeet_model))
        self.ctx = None
        self.lock = threading.Lock()

    async def start(self) -> None:
        if self.ctx is None:
            await asyncio.to_thread(self._load)

    def _load(self) -> None:
        lib = C.CDLL(str(self.lib_path))
        lib.parakeet_context_default_params_by_ref.restype = C.POINTER(_CtxParams)
        lib.parakeet_init_from_file_with_params.argtypes = [C.c_char_p, _CtxParams]
        lib.parakeet_init_from_file_with_params.restype = C.c_void_p
        lib.parakeet_full_default_params_by_ref.argtypes = [C.c_int]
        lib.parakeet_full_default_params_by_ref.restype = C.POINTER(_FullParams)
        lib.parakeet_full.argtypes = [C.c_void_p, _FullParams, C.POINTER(C.c_float), C.c_int]
        lib.parakeet_full.restype = C.c_int
        lib.parakeet_full_n_segments.argtypes = [C.c_void_p]
        lib.parakeet_full_get_segment_text.argtypes = [C.c_void_p, C.c_int]
        lib.parakeet_full_get_segment_text.restype = C.c_char_p
        cp = lib.parakeet_context_default_params_by_ref().contents
        cp.use_gpu = True
        self.ctx = lib.parakeet_init_from_file_with_params(self.model.encode(), cp)
        if not self.ctx:
            raise RuntimeError(f"failed to load {self.model}")
        fp = lib.parakeet_full_default_params_by_ref(0).contents
        fp.n_threads = 4
        fp.no_context = True
        self.params = _FullParams.from_buffer_copy(fp)
        self.lib = lib

    def _run(self, pcm: np.ndarray) -> str:
        buf = np.ascontiguousarray(pcm, dtype=np.float32)
        with self.lock:
            rc = self.lib.parakeet_full(self.ctx, self.params, buf.ctypes.data_as(C.POINTER(C.c_float)), len(buf))
            if rc != 0:
                raise RuntimeError(f"parakeet_full failed: {rc}")
            n = self.lib.parakeet_full_n_segments(self.ctx)
            return "".join(self.lib.parakeet_full_get_segment_text(self.ctx, i).decode(errors="replace")
                           for i in range(n)).strip()

    async def transcribe(self, pcm: np.ndarray, prompt: str = "", languages: list[str] | None = None) -> ASRResult:
        await self.start()
        t0 = time.perf_counter()
        if len(pcm) < 16000:  # model needs a little context; pad short clips with silence
            pcm = np.concatenate([pcm, np.zeros(16000 - len(pcm), np.float32)])
        text = await asyncio.to_thread(self._run, pcm)
        return ASRResult(text=text, seconds=time.perf_counter() - t0)
