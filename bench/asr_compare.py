"""Compare ASR engines on your own recordings from the history (no ground truth: eyeball the columns).
Needs: whisper-server on :8942 and llama-server (Qwen3-ASR) on :8943, e.g. started by hand."""
import asyncio
import math
import sys
import time

import httpx
import soundfile as sf

from whisprfake import config as C
from whisprfake.asr.base import wav_bytes
from whisprfake.asr.parakeet_cpp import ParakeetCpp
from whisprfake.pipeline.dictionary import asr_prompt
from whisprfake.store.db import Store


async def whisper(c, audio, lang, prompt, ctx):
    t = time.perf_counter()
    data = {"response_format": "json", "temperature": "0.0", "language": lang, "no_timestamps": "true"}
    if prompt:
        data["prompt"] = prompt
    if ctx:
        data["audio_ctx"] = str(ctx)
    r = await c.post("http://127.0.0.1:8942/inference", data=data, files={"file": ("a.wav", wav_bytes(audio))})
    return r.json().get("text", "").strip(), time.perf_counter() - t


async def qwen(c, audio, prompt):
    t = time.perf_counter()
    data = {"model": "q", "response_format": "json"}
    if prompt:
        data["prompt"] = prompt
    r = await c.post("http://127.0.0.1:8943/v1/audio/transcriptions", data=data, files={"file": ("a.wav", wav_bytes(audio))})
    txt = r.json().get("text", "")
    return txt.split("<asr_text>")[-1].strip(), time.perf_counter() - t


async def main():
    st = Store()
    prompt = asr_prompt(st.terms())
    ids = [int(x) for x in sys.argv[1:]]
    rows = st.q("SELECT id, audio_path, raw FROM dictations WHERE audio_path IS NOT NULL AND mode='dictate' ORDER BY id")
    rows = [r for r in rows if not ids or r["id"] in ids]
    pk = ParakeetCpp(C.load())
    await pk.start()
    c = httpx.AsyncClient(timeout=60)
    tot = {k: 0.0 for k in ("pk", "wde", "wauto", "q")}
    for r in rows:
        a, _ = sf.read(r["audio_path"], dtype="float32")
        dur = len(a) / 16000
        ctx = min(1500, int(math.ceil(dur * 50)) + 128)
        p = await pk.transcribe(a)
        w1 = await whisper(c, a, "de", prompt, ctx)
        w2 = await whisper(c, a, "auto", prompt, ctx)
        q = await qwen(c, a, prompt)
        for k, v in (("pk", p.seconds), ("wde", w1[1]), ("wauto", w2[1]), ("q", q[1])):
            tot[k] += v
        print(f"#{r['id']} {dur:4.1f}s")
        print(f"   parakeet {p.seconds*1000:4.0f}ms | {p.text[:110]}")
        print(f"   whis-de  {w1[1]*1000:4.0f}ms | {w1[0][:110]}")
        print(f"   whis-aut {w2[1]*1000:4.0f}ms | {w2[0][:110]}")
        print(f"   qwen3asr {q[1]*1000:4.0f}ms | {q[0][:110]}")
    n = max(1, len(rows))
    print("avg ms:", {k: round(v / n * 1000) for k, v in tot.items()})


asyncio.run(main())
