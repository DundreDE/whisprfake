"""Speaker diarization in a separate process: sherpa-onnx needs onnxruntime 1.28.2 while the daemon uses
the onnxruntime wheel; keeping them in different processes avoids two runtimes in one address space.
Usage: python -m whisprfake.notetaker.diarize_proc audio.wav  → JSON [[start, end, speaker], ...]"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .. import config as C

ORT_VERSION = "1.28.2"
ORT_DIR = C.MODELS_DIR / f"onnxruntime-linux-x64-{ORT_VERSION}" / "lib"


def ensure_link() -> None:
    import importlib.util

    spec = importlib.util.find_spec("sherpa_onnx")
    if spec is None or not ORT_DIR.exists():
        return
    link = Path(spec.origin).parent / "lib" / "libonnxruntime.so"
    target = ORT_DIR / f"libonnxruntime.so.{ORT_VERSION}"
    if link.is_symlink() and link.resolve() == target.resolve():
        return
    link.unlink(missing_ok=True)
    link.symlink_to(target)


def main() -> None:
    import numpy as np
    import soundfile as sf

    ensure_link()
    import sherpa_onnx

    audio, _ = sf.read(sys.argv[1], dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    M = C.MODELS_DIR
    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(M / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx")), num_threads=6),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(M / "3dspeaker_eres2net_base.onnx"),
                                                              num_threads=6),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=int(sys.argv[2]) if len(sys.argv) > 2 else -1,
                                                    threshold=0.6),
        min_duration_on=0.3, min_duration_off=0.5,
    )
    res = sherpa_onnx.OfflineSpeakerDiarization(cfg).process(np.ascontiguousarray(audio)).sort_by_start_time()
    json.dump([[r.start, r.end, r.speaker] for r in res], sys.stdout)


if __name__ == "__main__":
    main()
