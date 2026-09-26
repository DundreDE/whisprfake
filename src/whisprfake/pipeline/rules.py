"""Deterministic text rules that run before/after the LLM (German + English).

Line-break commands are turned into placeholders before the LLM sees the text so a small model
cannot lose them; `restore_placeholders` turns them back into real newlines afterwards.
"""

from __future__ import annotations

import re

NL, PARA = "⟦NL⟧", "⟦PARA⟧"

_W = r"(?<![\w-])"   # word start
_E = r"(?![\w-])"    # word end

_BREAKS = [
    (PARA, r"(?:neuer|neuen)\s+absatz|absatz\s+einfügen|new\s+paragraph|start\s+a\s+new\s+paragraph|skip\s+a\s+line"),
    (NL, r"neue\s+zeile|nächste\s+zeile|zeilenumbruch|new\s+line|next\s+line|line\s+break"),
]

# Unambiguous spoken punctuation (safe to apply even without the LLM).
_PUNCT = [
    ("?", r"fragezeichen|question\s+mark"),
    ("!", r"ausrufezeichen|ausrufungszeichen|exclamation\s+(?:point|mark)"),
    (":", r"doppelpunkt|colon"),
    (";", r"semikolon|semicolon"),
    (",", r"komma|comma"),
    ("…", r"auslassungspunkte|ellipsis"),
    (" – ", r"gedankenstrich|em\s+dash"),
]
# "Punkt"/"period"/"full stop" are real words too; only treat them as punctuation at the very end.
_TRAILING_PERIOD = re.compile(rf"\s*[,.]?\s*{_W}(?:punkt|period|full\s+stop){_E}\s*[.]?\s*$", re.I)

_SUBMIT = re.compile(
    rf"[\s,.;:!?]*{_W}(?:press\s+enter|hit\s+enter|and\s+send|drück\s+enter|enter\s+drücken|und\s+absenden|abschicken)"
    rf"{_E}[\s.!]*$",
    re.I,
)

_FILLERS = re.compile(
    # plain "um" is a German preposition, so only "umm" counts; the LLM handles English "um".
    rf"[,]?\s*{_W}(?:ähm+|äh+|öhm+|öh+|hmm+|hm|uhm+|uh+|umm+|erm+|mhm)(?![\w-])[,]?",
    re.I,
)


def extract_submit(text: str) -> tuple[str, bool]:
    """Strip a trailing 'press enter' / 'drück Enter' command. Returns (text, submit)."""
    m = _SUBMIT.search(text)
    if not m:
        return text, False
    return text[: m.start()].rstrip(), True


def protect_breaks(text: str) -> str:
    for token, pat in _BREAKS:
        text = re.sub(rf"\s*{_W}(?:{pat}){_E}[\s,.]*", f" {token} ", text, flags=re.I)
    return re.sub(r"[ \t]+", " ", text).strip()


def restore_placeholders(text: str) -> str:
    text = re.sub(rf"\s*{re.escape(PARA)}\s*", "\n\n", text)
    text = re.sub(rf"\s*{re.escape(NL)}\s*", "\n", text)
    # Capitalize the first letter after a break (sentence start).
    return re.sub(r"(\n+)(\w)", lambda m: m.group(1) + m.group(2).upper(), text).strip()


def spoken_punctuation(text: str) -> str:
    for sym, pat in _PUNCT:
        if sym.strip() in ",;:?!…":
            text = re.sub(rf"\s*[,.]?\s*{_W}(?:{pat}){_E}\s*[,.]?", sym, text, flags=re.I)
        else:
            text = re.sub(rf"\s*{_W}(?:{pat}){_E}\s*", sym, text, flags=re.I)
    text = _TRAILING_PERIOD.sub(".", text)
    # spacing after punctuation
    text = re.sub(r"([,;:?!])(?=[^\s\n,;:?!.)\]\"'])", r"\1 ", text)
    return text


def remove_fillers(text: str) -> str:
    text = _FILLERS.sub("", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.;:?!])", r"\1", text)
    text = re.sub(r"^[,\s]+", "", text)
    return text.strip()


def tidy(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([,.;:?!])", r"\1", text)
    text = re.sub(r"([,;:])\1+", r"\1", text)
    text = re.sub(r"\.{2}(?!\.)", ".", text)
    return text.strip()


def light_clean(text: str) -> str:
    """Rule-only cleanup used for level 'light' and as the fallback when the LLM guard fails."""
    text = remove_fillers(text)
    text = spoken_punctuation(text)
    text = tidy(text)
    if text and text[0].islower() and not text.startswith(("⟦",)):
        text = text[0].upper() + text[1:]
    return text


_NEEDS_LLM = re.compile(
    rf"{_W}(?:actually|scratch\s+that|never\s+mind|i\s+mean|wait|no\s+wait|nein\s+warte|warte|"
    rf"ich\s+meine|streich\s+das|vergiss\s+das|korrektur|eigentlich|nein|sorry|"
    rf"eins|zwei|drei|erstens|zweitens|first|second|third|one|two|three|"
    rf"punkt|period|äh+|ähm+|uh+|um+|also|halt|quasi|sozusagen|like|you\s+know){_E}",
    re.I,
)


def needs_llm(text: str) -> bool:
    """Heuristic: short dictations without fillers/backtrack/list cues can skip the LLM."""
    words = len(text.split())
    return words > 12 or bool(_NEEDS_LLM.search(text))


_LIST_ITEM = re.compile(r"^(\s*(?:\d+[.)]|[-*•])\s+.*?)[.]\s*$", re.M)


def normalize_lines(text: str) -> str:
    """Strip trailing spaces (Markdown hard breaks) and final periods of list items."""
    text = re.sub(r"[ \t]+$", "", text, flags=re.M)
    return _LIST_ITEM.sub(r"\1", text)
