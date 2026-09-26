"""Run the cleanup pipeline over bench/cleanup_cases.jsonl for one or more Ollama models."""
import asyncio
import json
import statistics
import sys
from pathlib import Path

from whisprfake.llm.ollama import Ollama
from whisprfake.pipeline import cleanup
from whisprfake.pipeline.dictionary import Term

_FILE = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].endswith(".jsonl") else "cleanup_cases.jsonl"
CASES = [json.loads(l) for l in Path(__file__).with_name(Path(_FILE).name).read_text().splitlines() if l.strip()]
from whisprfake.store.seed import TECH_TERMS
TERMS = [Term(t, s) for t, s in TECH_TERMS]


async def main(models: list[str], level: str = "medium"):
    from whisprfake.pipeline import lexicon
    lexicon.get().load()
    llm = Ollama("http://127.0.0.1:11434")
    for model in models:
        await llm.warm(model)
        times = []
        print(f"\n=== {model} ({level}) ===")
        for c in CASES:
            ctx = cleanup.Context(app="test", category=c["category"])
            r = await cleanup.process(c["raw"], llm=llm, model=model, level=level, style=c["style"], ctx=ctx,
                                      terms=TERMS, snips=[], timeout=20)
            times.append(r.llm_seconds)
            flag = "" if r.guard_reason in ("ok", "") else f"  [GUARD: {r.guard_reason}]"
            print(f"{r.llm_seconds*1000:6.0f} ms | {c['raw']}\n          -> {r.text!r}{flag}")
        print(f"median {statistics.median(times)*1000:.0f} ms, max {max(times)*1000:.0f} ms")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.endswith(".jsonl")]
    asyncio.run(main(args or ["qwen3:4b-instruct-2507-q4_K_M"]))
