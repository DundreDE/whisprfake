"""Microphone capture with pre-warm ring buffer (so the first syllable isn't lost) and level metering."""

from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Callable

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

SR = 16000
BLOCK = 512  # 32 ms


def pick_device(priority: list[str]) -> int | None:
    """First input device whose name contains one of the priority substrings; None = system default."""
    try:
        devices = sd.query_devices()
    except Exception:
        return None
    for want in priority:
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0 and want.lower() in d["name"].lower():
                return i
    return None


def list_inputs() -> list[str]:
    return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]


class Recorder:
    def __init__(self, priority: list[str] | None = None, prewarm_s: float = 0.35,
                 on_level: Callable[[float], None] | None = None):
        self.priority = priority or []
        self.on_level = on_level
        self.stream: sd.InputStream | None = None
        self.ring: collections.deque[np.ndarray] = collections.deque(maxlen=max(1, int(prewarm_s * SR / BLOCK)))
        self.recording = False
        self.blocks: list[np.ndarray] = []
        self.listeners: list[Callable[[np.ndarray], None]] = []
        self.lock = threading.Lock()
        self.last_open = 0.0
        self.error: str | None = None

    # -- stream lifecycle ------------------------------------------------
    def open(self) -> None:
        with self.lock:
            self.last_open = time.monotonic()
            if self.stream is not None:
                return
            dev = pick_device(self.priority)
            self.error = None
            self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=BLOCK,
                                         device=dev, callback=self._cb, latency="low")
            self.stream.start()
            log.debug("mic open (device=%s)", dev)

    def close_if_idle(self, idle_s: float = 3.0) -> None:
        with self.lock:
            if self.stream and not self.recording and time.monotonic() - self.last_open > idle_s:
                self.stream.stop()
                self.stream.close()
                self.stream = None
                self.ring.clear()
                log.debug("mic closed")

    def _cb(self, indata, frames, t, status) -> None:
        if status:
            log.debug("audio status: %s", status)
        block = indata[:, 0].copy()
        if self.recording:
            self.blocks.append(block)
            for fn in self.listeners:
                fn(block)
            if self.on_level:
                rms = float(np.sqrt(np.mean(block * block)) + 1e-9)
                self.on_level(min(1.0, max(0.0, (20 * np.log10(rms) + 60) / 50)))  # -60..-10 dBFS → 0..1
        else:
            self.ring.append(block)

    # -- recording -------------------------------------------------------
    def start(self) -> np.ndarray:
        """Begin recording; returns the pre-roll audio captured before the key press."""
        self.open()
        with self.lock:
            pre = np.concatenate(list(self.ring)) if self.ring else np.zeros(0, np.float32)
            self.ring.clear()
            self.blocks = [pre] if len(pre) else []
            self.recording = True
        return pre

    def stop(self) -> np.ndarray:
        with self.lock:
            self.recording = False
            audio = np.concatenate(self.blocks) if self.blocks else np.zeros(0, np.float32)
            self.blocks = []
            self.last_open = time.monotonic()
        return audio
