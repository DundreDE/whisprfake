"""Start/stop/cancel sounds (generated once, played with pw-play)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import soundfile as sf

from ..config import DATA_DIR

SOUNDS = DATA_DIR / "sounds"
SR = 48000


def _tone(freqs: list[tuple[float, float]], vol: float = 0.35) -> np.ndarray:
    out = []
    for f, dur in freqs:
        t = np.arange(int(SR * dur)) / SR
        env = np.minimum(1, t / 0.006) * np.exp(-t / (dur * 0.45))
        out.append(vol * env * (np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t)))
    return np.concatenate(out).astype(np.float32)


def ensure_sounds() -> None:
    SOUNDS.mkdir(parents=True, exist_ok=True)
    spec = {
        "start": [(880, 0.07), (1318.5, 0.09)],
        "stop": [(1318.5, 0.06), (987.8, 0.08)],
        "cancel": [(440, 0.08), (330, 0.1)],
        "lock": [(880, 0.05), (1174.7, 0.05), (1568, 0.08)],
        "command": [(659.3, 0.06), (987.8, 0.06), (1318.5, 0.08)],
        "error": [(220, 0.15)],
        "warn": [(1046.5, 0.1), (1046.5, 0.1)],
    }
    for name, tones in spec.items():
        p = SOUNDS / f"{name}.wav"
        if not p.exists():
            sf.write(p, _tone(tones), SR)


class Sounds:
    def __init__(self, enabled: bool = True, volume: float = 0.5):
        self.enabled, self.volume = enabled, volume
        ensure_sounds()

    def play(self, name: str) -> None:
        if not self.enabled:
            return
        p: Path = SOUNDS / f"{name}.wav"
        if p.exists():
            asyncio.get_running_loop().create_task(self._play(p))

    async def _play(self, p: Path) -> None:
        proc = await asyncio.create_subprocess_exec("pw-play", "--volume", f"{self.volume:.2f}", str(p),
                                                    stdout=asyncio.subprocess.DEVNULL,
                                                    stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()
