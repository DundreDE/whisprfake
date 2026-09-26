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


_EN_UM = re.compile(r"[,]?\s*(?<![\w-])(?:um|uh|er)(?![\w-])[,]?", re.I)


def light_clean(text: str, english: bool = False) -> str:
    """Rule-only cleanup used for level 'light' and as the fallback when the LLM guard fails."""
    text = remove_fillers(text)
    if english:  # "um" is only a filler in English (in German it's a preposition)
        text = _EN_UM.sub("", text)
    text = spoken_punctuation(text)
    text = tidy(text)
    text = re.sub(r"([.!?]\s+)([a-zäöü])", lambda m: m.group(1) + m.group(2).upper(), text)
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


# ---------------------------------------------------------------------------- list formatting fallback
_ORD_DE = ["erstens", "zweitens", "drittens", "viertens", "fünftens", "sechstens", "siebtens", "achtens", "neuntens",
           "zehntens"]
_ORD_EN = ["firstly", "secondly", "thirdly", "fourthly", "fifthly"]
_ORD_EN_PLAIN = ["first", "second", "third", "fourth", "fifth", "sixth"]
_NUM_DE = ["eins", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun", "zehn"]
_BULLET_WORDS = r"stichpunkt|spiegelstrich|nächster\s+punkt|neuer\s+punkt|aufzählungspunkt|bullet\s+point|next\s+point"
_BOUNDARY = r"(?:^|(?<=[.!?:;,])\s*|(?<=\n)|(?<=\s)(?:and|und|sowie|then|dann)\s+)"


def _ordinal_pattern() -> re.Pattern:
    alts = "|".join(_ORD_DE + _ORD_EN) + "|" + "|".join(rf"{w}(?=\s*[,:])" for w in _ORD_EN_PLAIN) \
        + "|" + "|".join(rf"punkt\s+{w}" for w in _NUM_DE)
    return re.compile(rf"{_BOUNDARY}\s*(?<![\w-])({alts})(?![\w-])\s*[,:]?\s*", re.I)


_ORD = _ordinal_pattern()
_BUL = re.compile(rf"(?<![\w-])(?:{_BULLET_WORDS})(?![\w-])\s*[,:]?\s*", re.I)


def _rank(word: str) -> int:
    w = re.sub(r"\s+", " ", word.lower())
    for lst in (_ORD_DE, _ORD_EN, _ORD_EN_PLAIN):
        if w in lst:
            return lst.index(w)
    if w.startswith("punkt "):
        return _NUM_DE.index(w.split(" ", 1)[1]) if w.split(" ", 1)[1] in _NUM_DE else -1
    return -1


def _items_to_lines(lead: str, items: list[str], numbered: bool) -> str:
    clean = []
    for it in items:
        it = re.sub(r"^(?:und|and|sowie|und\s+dann|then)\s+", "", it.strip(" ,;:"), flags=re.I).rstrip(" .;,")
        if it:
            clean.append(it[0].upper() + it[1:])
    if len(clean) < 2:
        return ""
    lead = lead.strip().rstrip(" ,;:.")
    lines = [f"{i + 1}. {it}" if numbered else f"- {it}" for i, it in enumerate(clean)]
    return ((lead + ":\n") if lead else "") + "\n".join(lines)


_LIST_WORDS = re.compile(
    r"(?<![\w-])(?:punkte|liste|to-?dos|aufgaben|schritte|vorteile|nachteile|pros?|cons?|steps|items|points|"
    r"tasks|features|anforderungen|themen|agenda|zutaten|einkaufsliste|optionen|options|ideen|ideas|gründe|reasons)(?![\w-])",
    re.I)
_ITEM_SPLIT = re.compile(r"\s*,\s*(?:(?:und|and|sowie|oder|or)\s+)?|\s+(?:und|and|sowie)\s+", re.I)


def _colon_lists(text: str) -> str:
    """'…folgende Punkte: A, B, C und D.' → lead-in + bullet lines (per sentence)."""
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])", text.strip())
    out, changed = [], False
    for sent in sentences:
        m = re.match(r"^(?P<lead>[^:\n]{3,160}):\s+(?P<body>[^:\n]+?)[.!]?$", sent)
        if m:
            items = [i for i in _ITEM_SPLIT.split(m.group("body")) if i and i.strip()]
            words_ok = all(len(i.split()) <= 9 for i in items)
            if words_ok and (len(items) >= 3 or (len(items) >= 2 and _LIST_WORDS.search(m.group("lead")))):
                block = _items_to_lines(m.group("lead"), items, numbered=False)
                if block:
                    out.append(block)
                    changed = True
                    continue
        out.append(sent)
    if not changed:
        return text
    # lists stand on their own; keep ordinary sentences as paragraphs around them
    res = ""
    for part in out:
        if not res:
            res = part
        elif "\n" in part or "\n" in res.split("\n\n")[-1]:
            res += "\n\n" + part
        else:
            res += " " + part
    return res


def format_lists(text: str) -> str:
    """Deterministic safety net: 'Erstens …, zweitens …' / 'Stichpunkt … Stichpunkt …' / 'folgende Punkte:
    A, B und C' → a real list, in case the LLM left it as running text."""
    if re.search(r"^\s*(?:\d+[.)]|[-•*])\s", text, re.M):
        return text  # already a list
    ords = list(_ORD.finditer(text))
    ranks = [_rank(m.group(1)) for m in ords]
    if len(ords) >= 2 and ranks[:2] == [0, 1]:
        # use the longest increasing run starting at 'first'
        run = [ords[0]]
        for m, r in zip(ords[1:], ranks[1:]):
            if r == len(run):
                run.append(m)
        items = [text[a.end():b.start()] for a, b in zip(run, run[1:])]
        tail = text[run[-1].end():]
        # the last item ends at the end of its sentence
        m_end = re.search(r"[.!?](\s+|$)", tail)
        last, rest = (tail[:m_end.start()], tail[m_end.end():]) if m_end and m_end.end() < len(tail) else (tail, "")
        out = _items_to_lines(text[:run[0].start()], items + [last], numbered=False)
        if out:
            return out + (("\n\n" + rest.strip()) if rest.strip() else "")
    buls = list(_BUL.finditer(text))
    if len(buls) >= 2:
        items = [text[a.end():b.start()] for a, b in zip(buls, buls[1:])] + [text[buls[-1].end():]]
        out = _items_to_lines(text[:buls[0].start()], items, numbered=False)
        if out:
            return out
    return _colon_lists(text)


_BULLET_LINE = re.compile(r"^(\s*)(?:[-*•–·]|•)\s+", re.M)


def set_bullets(text: str, bullet: str) -> str:
    return _BULLET_LINE.sub(lambda m: f"{m.group(1)}{bullet} ", text)


# ---------------------------------------------------------------------------- keep the speaker's anglicisms
# (anglicism the speaker used, German synonym small LLMs like to swap in) – infinitive and participle
_GERMANIZED = [
    ("updaten", "aktualisieren"), ("geupdatet", "aktualisiert"), ("fixen", "beheben"), ("gefixt", "behoben"),
    ("reviewen", "überprüfen"), ("reviewen", "prüfen"), ("checken", "überprüfen"), ("checken", "prüfen"),
    ("gecheckt", "überprüft"), ("downloaden", "herunterladen"), ("uploaden", "hochladen"), ("deployen", "bereitstellen"),
    ("debuggen", "Fehler suchen"), ("committen", "einchecken"), ("pushen", "hochladen"), ("mergen", "zusammenführen"),
    ("canceln", "absagen"), ("chatten", "schreiben"), ("liken", "mögen"), ("posten", "veröffentlichen"),
]


def keep_anglicisms(raw: str, out: str) -> str:
    raw_l = raw.lower()
    for eng, ger in _GERMANIZED:
        if re.search(rf"(?<![\w-]){eng}(?![\w-])", raw_l) and not re.search(rf"(?<![\w-]){eng}(?![\w-])", out, re.I):
            out, n = re.subn(rf"(?<![\w-]){ger}(?![\w-])", eng, out, count=1, flags=re.I)
    return out
