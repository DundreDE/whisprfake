"""Sanity checks on LLM cleanup output.

A cleanup model must *rewrite* the dictation, never answer it. If the output looks like an answer,
a refusal, or dropped placeholders, the caller falls back to the rule-cleaned raw text.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

PREAMBLES = re.compile(
    r"^\s*(?:hier\s+ist|hier\s+die|gerne|natürlich|klar[,!]|sicher[,!]|okay[,!]|"
    r"here\s+is|here's|sure[,!]|certainly|of\s+course|cleaned\s+(?:up\s+)?text|bereinigter\s+text|"
    r"output|ausgabe)\b[^\n]*?:\s*",
    re.I,
)
_PLACEHOLDER = re.compile(r"⟦[A-Z]+\d*⟧")
_WORD = re.compile(r"[\wÄÖÜäöüß]+", re.U)


def strip_wrapping(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^<think>.*?</think>\s*", "", text, flags=re.S)
    text = PREAMBLES.sub("", text, count=1)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'„“`":
        text = text[1:-1].strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.strip("`").strip()
    return text


def _words(t: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(t)]


def overlap(src: str, out: str) -> float:
    """Share of output words that (fuzzily) appear in the source."""
    ow, sw = _words(out), set(_words(src))
    if not ow:
        return 0.0
    hit = 0
    for w in ow:
        if w in sw or w.isdigit() or any(fuzz.ratio(w, s) >= 80 for s in sw if abs(len(s) - len(w)) <= 3):
            hit += 1
    return hit / len(ow)


def check(src: str, out: str, level: str = "medium") -> tuple[bool, str]:
    """Return (ok, reason)."""
    if not out.strip():
        return False, "empty"
    if sorted(_PLACEHOLDER.findall(src)) != sorted(_PLACEHOLDER.findall(out)):
        return False, "placeholders"
    sw, ow = len(_words(src)), len(_words(out))
    lo, hi = (0.25, 2.0) if level == "high" else (0.35, 1.6)
    if sw >= 6 and not (lo <= ow / max(sw, 1) <= hi):
        return False, f"length {ow}/{sw}"
    if sw < 6 and ow > sw * 2 + 6:
        return False, f"length {ow}/{sw}"
    min_overlap = 0.55 if level == "high" else 0.7
    ov = overlap(src, out)
    if ov < min_overlap:
        return False, f"overlap {ov:.2f}"
    return True, "ok"
