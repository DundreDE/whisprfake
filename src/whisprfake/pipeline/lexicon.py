"""Existing dictionaries loaded into whisprfake:

* **Hunspell German + English** (LibreOffice / igerman98): tells us which words exist. A word that exists in
  neither language (and isn't in your dictionary) was most likely misheard – "Gestellenhacker", "Medikinate".
  Only those get the looser dictionary matching and are pointed out to the LLM.
* **Anglicisms in Duden spelling** (data/anglicisms.txt + your own ~/.config/whisprfake/anglicisms.txt):
  in German text every variant is normalized – "email"/"e mail" → "E-Mail", "know how" → "Know-how",
  "home office" → "Homeoffice", "pull request" → "Pull-Request", "meeting" → "Meeting".
"""

from __future__ import annotations

import logging
import re
import threading
from importlib import resources

from .. import config as C

log = logging.getLogger(__name__)
LEXICON_DIR = C.MODELS_DIR / "lexicon"
USER_ANGLICISMS = C.CONFIG_DIR / "anglicisms.txt"
_WORD = re.compile(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß'-]*")


def _key(s: str) -> str:
    return re.sub(r"[\s\-]+", "", s.lower())


def _load_anglicisms() -> dict[str, str]:
    lines = resources.files("whisprfake.data").joinpath("anglicisms.txt").read_text().splitlines()
    if USER_ANGLICISMS.exists():
        lines += USER_ANGLICISMS.read_text().splitlines()
    out: dict[str, str] = {}
    for line in lines:
        w = line.strip()
        if w and not w.startswith("#"):
            out[_key(w)] = w  # later (user) entries win
    return out


class Lexicon:
    def __init__(self):
        self.de = self.en = None
        self.anglicisms = _load_anglicisms()
        self.max_parts = max((len(re.split(r"[\s-]+", v)) for v in self.anglicisms.values()), default=1)
        self.ready = threading.Event()

    def load(self) -> None:
        """Load Hunspell dictionaries (~1.5 s); call in a background thread."""
        try:
            from spylls.hunspell import Dictionary

            de, en = LEXICON_DIR / "de_DE_frami", LEXICON_DIR / "en_US"
            if de.with_suffix(".dic").exists():
                self.de = Dictionary.from_files(str(de))
            if en.with_suffix(".dic").exists():
                self.en = Dictionary.from_files(str(en))
        except Exception as e:
            log.warning("hunspell dictionaries not loaded: %s", e)
        self.ready.set()

    # -------------------------------------------------------------- word knowledge
    def known(self, word: str) -> bool:
        if not self.ready.is_set() or (self.de is None and self.en is None):
            return True  # without dictionaries, assume everything is fine
        if _key(word) in self.anglicisms:
            return True
        for w in {word, word.lower(), word.capitalize()}:
            for d in (self.de, self.en):
                try:
                    if d is not None and d.lookup(w):
                        return True
                except Exception:
                    pass
        if "-" in word:  # compounds like "Pull-Request-Beschreibung"
            return all(self.known(p) for p in word.split("-") if p)
        return False

    def unknown_words(self, text: str, extra_known: set[str] | None = None) -> list[str]:
        """Words that exist in no loaded dictionary – likely misheard (names aside)."""
        extra = {w.lower() for w in (extra_known or set())}
        out = []
        for m in _WORD.finditer(text):
            w = m.group(0).strip("-'")
            if len(w) < 4 or w.isupper() or w.lower() in extra:
                continue
            if not self.known(w):
                out.append(w)
        return list(dict.fromkeys(out))

    # -------------------------------------------------------------- anglicisms
    _CODE = re.compile(r"`[^`]*`|\"[^\"]*\"|(?<![\w-])(?:git|npm|pnpm|yarn|uv|pip|docker|kubectl|cargo|make|sudo|pacman|"
                       r"yay|systemctl|python3?|node|bun|deno|go|ssh|curl|cd|ls)\s[^.,;!?\n]*|(?<!\w)--?\w[\w-]*")

    def normalize_anglicisms(self, text: str) -> str:
        """German text only: fix spelling variants of anglicisms. Never lowercases a capitalized word and never
        touches commands, flags, quotes or code."""
        protected = [m.span() for m in self._CODE.finditer(text)]
        tokens = [t for t in re.finditer(r"[A-Za-zÄÖÜäöüß]+(?:-[A-Za-zÄÖÜäöüß]+)*", text)
                  if not any(a <= t.start() < b for a, b in protected)]
        out, i, last = [], 0, 0
        while i < len(tokens):
            done = False
            for n in range(min(self.max_parts, len(tokens) - i), 0, -1):
                span = tokens[i:i + n]
                # only join tokens separated by plain spaces/hyphens
                if any(text[span[k].end():span[k + 1].start()] not in (" ", "-") for k in range(n - 1)):
                    continue
                raw = text[span[0].start():span[-1].end()]
                target = self.anglicisms.get(_key(raw))
                if target is None or raw == target:
                    continue
                if raw[0].isupper() and target[0].islower():
                    target = target[0].upper() + target[1:]  # sentence start: keep capital
                if n == 1 and raw.lower() == target.lower() and raw[0].isupper():
                    continue  # only case differs and the ASR/LLM already capitalized: leave it
                out.append(text[last:span[0].start()] + target)
                last, i, done = span[-1].end(), i + n, True
                break
            if not done:
                i += 1
        out.append(text[last:])
        return "".join(out)


_LEXICON: Lexicon | None = None


def get() -> Lexicon:
    global _LEXICON
    if _LEXICON is None:
        _LEXICON = Lexicon()
    return _LEXICON


def ensure_downloaded() -> None:
    """Fetch the LibreOffice Hunspell files if missing (used by the daemon on first start)."""
    import urllib.request

    LEXICON_DIR.mkdir(parents=True, exist_ok=True)
    base = "https://raw.githubusercontent.com/LibreOffice/dictionaries/master/"
    for rel, name in [("de/de_DE_frami.dic", "de_DE_frami.dic"), ("de/de_DE_frami.aff", "de_DE_frami.aff"),
                      ("en/en_US.dic", "en_US.dic"), ("en/en_US.aff", "en_US.aff")]:
        p = LEXICON_DIR / name
        if not p.exists():
            try:
                urllib.request.urlretrieve(base + rel, p)
            except Exception as e:
                log.warning("could not download %s: %s", name, e)
                return
