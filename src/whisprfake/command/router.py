"""Command Mode: decide what a spoken instruction means.

  * web search:  "search/ask/hey/such/frag <Google|Perplexity|ChatGPT|Claude> <query>"   → open browser
  * dictionary:  "füge X zum Wörterbuch hinzu" / "add X to the dictionary"                → store
  * transform:   "transform <name>" / "Transform <name>" / exact transform name, needs selection
  * edit:        anything else while text is selected                                    → rewrite selection
  * ask:         anything else without selection                                         → local answer popup
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass

from rapidfuzz import fuzz, process

SEARCH_ENGINES = {
    "google": "https://www.google.com/search?q={}",
    "perplexity": "https://www.perplexity.ai/search?q={}",
    "chatgpt": "https://chatgpt.com/?q={}",
    "chat gpt": "https://chatgpt.com/?q={}",
    "claude": "https://claude.ai/new?q={}",
}

_SEARCH = re.compile(
    r"^\s*(?:search|ask|hey|such(?:e)?|frag(?:e)?|google)\s*(?:auf|in|bei|on|with|mit)?\s*"
    r"(google|perplexity|chat\s?gpt|claude)\b[\s,:]*(?:nach|for|about|ob|was|wie)?\s*(.*)$",
    re.I | re.S,
)
_DICT = [
    re.compile(r"^\s*(?:füg(?:e)?|nimm|pack)\s+(.+?)\s+(?:zum|ins|in das|in mein)\s+wörterbuch(?:\s+hinzu|\s+auf)?[\s.!]*$", re.I),
    re.compile(r"^\s*add\s+(.+?)\s+to\s+(?:the\s+|my\s+)?dictionary[\s.!]*$", re.I),
]
_TRANSFORM = re.compile(r"^\s*(?:transform(?:iere)?|wende)\s+(?:mit\s+|an\s+)?(.+?)(?:\s+an)?[\s.!]*$", re.I)


@dataclass
class Route:
    kind: str                 # search | dictionary | transform | edit | ask | noop
    arg: str = ""             # url / term / transform name / instruction
    prompt: str = ""          # transform prompt


def route(instruction: str, selection: str, transforms: dict[str, str]) -> Route:
    ins = instruction.strip().rstrip(".")
    if not ins:
        return Route("noop")
    if m := _SEARCH.match(ins):
        engine = re.sub(r"\s+", " ", m.group(1).lower())
        query = m.group(2).strip()
        if selection:
            query = f"{query} {selection}".strip()
        url = SEARCH_ENGINES.get(engine, SEARCH_ENGINES["google"]).format(urllib.parse.quote_plus(query))
        return Route("search", url)
    for rx in _DICT:
        if m := rx.match(ins):
            return Route("dictionary", m.group(1).strip(" \"'„“"))
    if selection and transforms:
        name = None
        if m := _TRANSFORM.match(ins):
            name = m.group(1)
        else:
            name = ins
        best = process.extractOne(name, list(transforms), scorer=fuzz.WRatio)
        if best and best[1] >= (80 if m else 92):
            return Route("transform", best[0], transforms[best[0]])
    if selection:
        return Route("edit", ins)
    return Route("ask", ins)
