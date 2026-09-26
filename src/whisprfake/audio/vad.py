"""Silero VAD (ONNX) + a chunker that cuts long recordings at pauses so ASR can run while the user
is still speaking; on release only the last chunk remains to be transcribed."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import onnxruntime as ort

FRAME = 512  # samples @ 16 kHz = 32 ms


class SileroVAD:
    def __init__(self, model_path: str):
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(model_path, sess_options=so, providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros(64, dtype=np.float32)

    def prob(self, frame: np.ndarray) -> float:
        x = np.concatenate([self.context, frame.astype(np.float32)])[None, :]
        out, self.state = self.sess.run(None, {"input": x, "state": self.state, "sr": np.array(16000, dtype=np.int64)})
        self.context = frame[-64:].astype(np.float32)
        return float(out[0][0])


@dataclass
class Chunker:
    vad: SileroVAD | None
    threshold: float = 0.5
    min_chunk_s: float = 4.0
    max_chunk_s: float = 25.0
    min_silence_s: float = 0.5

    buf: list[np.ndarray] = field(default_factory=list)
    pending: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32))
    n: int = 0                 # samples in current chunk
    speech_samples: int = 0    # speech in current chunk
    total_speech: int = 0
    silence_run: int = 0

    def feed(self, block: np.ndarray) -> list[np.ndarray]:
        """Feed raw samples; returns chunks that are ready for transcription."""
        ready: list[np.ndarray] = []
        self.pending = np.concatenate([self.pending, block])
        while len(self.pending) >= FRAME:
            frame, self.pending = self.pending[:FRAME], self.pending[FRAME:]
            p = self.vad.prob(frame) if self.vad else 1.0
            self.buf.append(frame)
            self.n += FRAME
            if p >= self.threshold:
                self.speech_samples += FRAME
                self.total_speech += FRAME
                self.silence_run = 0
            else:
                self.silence_run += FRAME
            secs = self.n / 16000
            cut = (secs >= self.min_chunk_s and self.speech_samples > 0.5 * 16000
                   and self.silence_run >= self.min_silence_s * 16000) or secs >= self.max_chunk_s
            if cut:
                ready.append(self._take())
        return ready

    def _take(self) -> np.ndarray:
        chunk = np.concatenate(self.buf) if self.buf else np.zeros(0, np.float32)
        self.buf, self.n, self.speech_samples, self.silence_run = [], 0, 0, 0
        return chunk

    def flush(self) -> np.ndarray | None:
        if len(self.pending):
            self.buf.append(self.pending)
            self.pending = np.zeros(0, np.float32)
        chunk = self._take()
        return chunk if len(chunk) else None

    @property
    def has_speech(self) -> bool:
        return self.total_speech > 0.25 * 16000 if self.vad else True
