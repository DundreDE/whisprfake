"""Auto-learn: after a dictation is inserted, look at the text field again a bit later. If the user
replaced a dictated word with a similar-looking one (a name or term the ASR got wrong), suggest the
corrected spelling for the dictionary (Wispr adds these automatically; we only suggest)."""

from __future__ import annotations

import difflib
import re

from rapidfuzz import fuzz

_TOKEN = re.compile(r"[\wÄÖÜäöüß][\wÄÖÜäöüß.+#-]*")


def _tokens(s: str) -> list[str]:
    return [t.rstrip(".") for t in _TOKEN.findall(s)]


def _locate(inserted: list[str], current: list[str]) -> list[str]:
    """The slice of `current` that best corresponds to the inserted text."""
    if not inserted or not current:
        return []
    sm = difflib.SequenceMatcher(a=current, b=inserted, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    if not blocks:
        # no word in common (e.g. "Post QSQL" → "PostgreSQL"): only usable when the field is short
        return current if len(current) <= len(inserted) * 2 + 2 else []
    start = max(0, blocks[0].a - blocks[0].b)
    end = min(len(current), blocks[-1].a + blocks[-1].size + (len(inserted) - blocks[-1].b - blocks[-1].size))
    return current[start:end]


def suggestions(inserted: str, current: str, known: set[str]) -> list[tuple[str, str]]:
    """Return (heard, corrected) pairs that look like vocabulary fixes."""
    a = _tokens(inserted)
    b = _locate(a, _tokens(current))
    if not b or len(b) > 2 * len(a) + 4:
        return []
    out: list[tuple[str, str]] = []
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op != "replace" or (i2 - i1) > 3 or (j2 - j1) > 2:
            continue
        heard, fixed = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        if fixed.lower() in known or heard.lower() == fixed.lower() or len(fixed) < 3:
            continue
        # A vocabulary fix changes spelling, not meaning: similar letters, and the fix looks like a name/term.
        similar = fuzz.ratio(heard.replace(" ", "").lower(), fixed.replace(" ", "").lower()) >= 55
        termish = fixed[0].isupper() or any(c.isupper() for c in fixed[1:]) or any(c.isdigit() for c in fixed)
        if similar and termish:
            out.append((heard, fixed))
    return out
