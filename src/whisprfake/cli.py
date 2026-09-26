"""whisprfake command line."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


def main() -> None:
    ap = argparse.ArgumentParser(prog="whisprfake", description="Local Wispr Flow for Hyprland")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("daemon", help="run the background service")
    sub.add_parser("hub", help="open the Hub (history, dictionary, snippets, settings)")
    sp = sub.add_parser("scratchpad", help="open the floating scratchpad")
    sp.add_argument("--note", type=int, help="open a specific note")
    sp.add_argument("--toggle", action="store_true", help="close if already focused")
    c = sub.add_parser("ctl", help="call a daemon method, e.g. `ctl toggle`, `ctl stats`")
    c.add_argument("method")
    c.add_argument("params", nargs="?", default="{}", help="JSON params")
    t = sub.add_parser("transcribe", help="transcribe (and clean) an audio file")
    t.add_argument("file")
    t.add_argument("--engine")
    t.add_argument("--raw", action="store_true", help="skip LLM cleanup")
    a = ap.parse_args()

    if a.cmd == "daemon":
        from .daemon.main import main as run

        run()
    elif a.cmd == "ctl":
        from .config import SOCKET_PATH
        from .daemon.ipc import call

        try:
            res = asyncio.run(call(SOCKET_PATH, a.method, json.loads(a.params)))
        except (FileNotFoundError, ConnectionRefusedError):
            sys.exit("whisprfake daemon is not running")
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    elif a.cmd == "transcribe":
        asyncio.run(_transcribe(a.file, a.engine, a.raw))
    elif a.cmd == "hub":
        from .ui.hub.app import main as hub

        hub()
    elif a.cmd == "scratchpad":
        from .ui.scratchpad.app import main as pad

        pad((["--note", str(a.note)] if a.note else []) + (["--toggle"] if a.toggle else []))


async def _transcribe(path: str, engine: str | None, raw: bool) -> None:
    import soundfile as sf

    from . import config as C
    from .asr import make_engine
    from .llm.ollama import Ollama
    from .pipeline import cleanup
    from .store.db import Store

    cfg = C.load()
    audio, sr = sf.read(path, dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if sr != 16000:
        import numpy as np

        idx = np.linspace(0, len(audio) - 1, int(len(audio) * 16000 / sr))
        audio = np.interp(idx, np.arange(len(audio)), audio).astype("float32")
    eng = make_engine(cfg, engine)
    await eng.start()
    try:
        r = await eng.transcribe(audio, languages=cfg.asr.languages)
        print(f"[{eng.name} {r.seconds*1000:.0f} ms, lang={r.language}] {r.text}")
        if not raw:
            st = Store()
            res = await cleanup.process(r.text, llm=Ollama(cfg.llm.base_url), model=cfg.llm.cleanup_model,
                                        level=cfg.cleanup.level, style="formal", ctx=cleanup.Context(),
                                        terms=st.terms(), snips=st.snippets())
            print(f"[cleanup {res.llm_seconds*1000:.0f} ms] {res.text}")
    finally:
        await eng.stop()


if __name__ == "__main__":
    main()
