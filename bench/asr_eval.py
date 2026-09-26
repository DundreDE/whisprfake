"""Re-transcribe your history recordings with the configured context-biased Qwen3-ASR and show them next to
what was recorded at the time. Usage: python bench/asr_eval.py [port-of-running-llama-server]"""
import asyncio
import statistics
import sys

import soundfile as sf

from whisprfake import config as C
from whisprfake.asr.qwen3_asr import Qwen3ASR
from whisprfake.pipeline.dictionary import asr_context
from whisprfake.store.db import Store


async def main():
    cfg, st = C.load(), Store()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else cfg.asr.port + 1
    eng = Qwen3ASR(cfg.asr.llama_server_bin, "", "", port)  # talk to an already running server
    ctx = asr_context(st.terms())
    times = []
    for r in st.q("SELECT id, audio_path, raw, asr_engine FROM dictations WHERE audio_path IS NOT NULL "
                  "AND mode='dictate' ORDER BY id"):
        a, _ = sf.read(r["audio_path"], dtype="float32")
        res = await eng.transcribe(a, prompt=ctx, languages=cfg.asr.languages)
        times.append(res.seconds)
        mark = " " if (r["raw"] or "").strip() == res.text else "*"
        print(f"{mark}#{r['id']:<3} {res.seconds*1000:4.0f}ms [{res.language}] {res.text[:90]!r}\n"
              f"        vorher ({r['asr_engine']}): {(r['raw'] or '')[:90]!r}")
    print(f"median {statistics.median(times)*1000:.0f} ms, max {max(times)*1000:.0f} ms")


asyncio.run(main())
