"""Personal dictionary: biases ASR, feeds the LLM a glossary, and fixes near-miss spellings afterwards
with phonetic matching (Kölner Phonetik for German, Metaphone for English)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import jellyfish
from rapidfuzz import fuzz


@dataclass
class Term:
    term: str
    sounds_like: list[str] = field(default_factory=list)  # explicit mishearings, e.g. ["super base"]
    starred: bool = False


_KP = {
    **dict.fromkeys("AEIJOUYÄÖÜ", "0"), "H": "", "B": "1", "F": "3", "V": "3", "W": "3",
    "G": "4", "K": "4", "Q": "4", "L": "5", "M": "6", "N": "6", "R": "7", "S": "8", "Z": "8",
}


def koelner(word: str) -> str:
    """Kölner Phonetik (Cologne phonetics)."""
    w = re.sub(r"[^A-ZÄÖÜß]", "", word.upper()).replace("ß", "S")
    codes = []
    for i, c in enumerate(w):
        prev = w[i - 1] if i else ""
        nxt = w[i + 1] if i + 1 < len(w) else ""
        if c in _KP:
            code = _KP[c]
        elif c == "P":
            code = "3" if nxt == "H" else "1"
        elif c in "DT":
            code = "8" if nxt in "CSZ" else "2"
        elif c == "C":
            if i == 0:
                code = "4" if nxt in "AHKLOQRUX" else "8"
            else:
                code = "8" if prev in "SZ" or nxt not in "AHKOQUX" else "4"
        elif c == "X":
            code = "8" if prev in "CKQ" else "48"
        else:
            code = ""
        codes.append(code)
    out = ""
    for code in codes:
        for ch in code:
            if not out or out[-1] != ch:
                out += ch
    return out[0] + out[1:].replace("0", "") if out else ""


def _phonetic_match(a: str, b: str) -> bool:
    a2, b2 = a.replace(" ", ""), b.replace(" ", "")
    if not a2 or not b2:
        return False
    kp = koelner(a2) == koelner(b2)
    mp = jellyfish.metaphone(a2) == jellyfish.metaphone(b2)
    return kp or mp


def asr_prompt(terms: list[Term], limit: int = 60) -> str:
    """Initial prompt / hotword context for Whisper/Qwen3-ASR (starred first)."""
    ordered = sorted(terms, key=lambda t: not t.starred)[:limit]
    return ", ".join(t.term for t in ordered)


def glossary(terms: list[Term]) -> str:
    return "\n".join(f"- {t.term}" + (f" (may be heard as: {', '.join(t.sounds_like)})" if t.sounds_like else "") for t in terms)


# Terms that are also ordinary words: their case is left to the LLM (it sees the sentence).
COMMON_WORDS = {"cursor", "react", "rust", "whisper", "python", "swift", "go", "cloud", "claude", "vulkan", "element",
                "signal", "slack", "notion", "linear", "arc", "zed", "helix", "tauri", "deno", "bun", "vite", "next"}


def _stop(tok: str) -> bool:
    from .lang import _DE, _EN

    return tok.lower() in _DE or tok.lower() in _EN


def _sound_ratio(a: str, b: str) -> float:
    return fuzz.ratio(jellyfish.metaphone(a), jellyfish.metaphone(b))


def _score(cand: str, term: str, tw: list[str]) -> int:
    """0 = no match, otherwise a similarity score (higher is better)."""
    joined, target = cand.replace(" ", "").lower(), term.replace(" ", "").lower()
    score = int(fuzz.ratio(joined, target))
    same_len = len(cand.split()) == len(tw)
    short = len(target) < 6
    if same_len and (score >= 88 or (score >= (82 if short else 72) and _phonetic_match(cand, term))
                     or (not short and score >= 70 and _sound_ratio(joined, target) >= 90)):
        return score
    # The ASR split an unknown word into several ("Post free SQL" -> "PostgreSQL"): same first letter and a
    # close spelling or sound required.
    toks = cand.split()
    if (len(toks) > len(tw) and len(target) >= 5 and joined[0] == target[0]
            and not any(_stop(t) for t in toks)):
        if (score >= 78 and joined[-1] == target[-1]) or (score >= 74 and _sound_ratio(joined, target) >= 85):
            return score
    return 0


def correct(text: str, terms: list[Term]) -> str:
    """Replace phonetic near-misses of dictionary terms (1-3 word windows)."""
    for t in terms:
        tw = t.term.split()
        # explicit sounds-like: exact case-insensitive replacement
        for s in t.sounds_like:
            text = re.sub(rf"(?<![\w]){re.escape(s)}(?![\w])", t.term, text, flags=re.I)
        if len(t.term.replace(" ", "")) < 4:
            continue
        tokens = list(re.finditer(r"[\wÄÖÜäöüß'-]+", text))
        cands: list[tuple[int, int, int]] = []  # (score, start, end)
        for n in {len(tw), len(tw) + 1, len(tw) + 2, max(1, len(tw) - 1)}:
            for j in range(len(tokens) - n + 1):
                span = tokens[j : j + n]
                cand = " ".join(x.group(0) for x in span)
                if cand == t.term:
                    continue
                if cand.lower() == t.term.lower():
                    sc = 0 if t.term.lower() in COMMON_WORDS else 100
                else:
                    sc = _score(cand, t.term, tw)
                if sc:
                    cands.append((sc, span[0].start(), span[-1].end()))
        used: list[tuple[int, int]] = []
        for _, s, e in sorted(cands, key=lambda c: -c[0]):
            if all(e <= us or s >= ue for us, ue in used):
                used.append((s, e))
        for s, e in sorted(used, reverse=True):
            text = text[:s] + t.term + text[e:]
    return text
