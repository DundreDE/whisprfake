"""Voice snippets: a spoken trigger phrase expands to a saved text block (Wispr: trigger <= 60 chars,
text <= 4000 chars). Triggers are swapped for placeholders before the LLM so the model can't touch
the expansion, then expanded afterwards."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

MAX_TRIGGER, MAX_TEXT = 60, 4000


@dataclass
class Snippet:
    trigger: str
    text: str


def _norm(s: str) -> list[str]:
    return re.findall(r"[\wÄÖÜäöüß]+", s.lower())


def protect(text: str, snippets: list[Snippet], threshold: int = 88) -> tuple[str, dict[str, str]]:
    """Replace spoken triggers with ⟦S<n>⟧. Returns (text, placeholder->expansion)."""
    mapping: dict[str, str] = {}
    for i, sn in enumerate(snippets):
        tw = _norm(sn.trigger)
        if not tw:
            continue
        # Build a regex over the original text that matches len(tw) consecutive words.
        tokens = list(re.finditer(r"[\wÄÖÜäöüß]+", text))
        n = len(tw)
        best = None
        for j in range(len(tokens) - n + 1):
            window = " ".join(t.group(0).lower() for t in tokens[j : j + n])
            score = fuzz.ratio(window, " ".join(tw))
            if score >= threshold and (best is None or score > best[0]):
                best = (score, tokens[j].start(), tokens[j + n - 1].end())
        if best:
            ph = f"⟦S{i}⟧"
            start, end = best[1], best[2]
            # swallow trailing punctuation the ASR attached to the trigger
            while end < len(text) and text[end] in ".,!?":
                end += 1
            text = text[:start] + ph + text[end:]
            mapping[ph] = sn.text[:MAX_TEXT]
    return text, mapping


def expand(text: str, mapping: dict[str, str]) -> str:
    for ph, val in mapping.items():
        text = text.replace(ph, val)
    return text
