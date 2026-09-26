"""Dictation post-processing: rules → LLM cleanup → guard → dictionary/snippets → context fix-ups."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from ..llm import prompts
from ..llm.ollama import Ollama
from . import dictionary, filerefs, guard, lang, lexicon, rules, snippets

log = logging.getLogger(__name__)


@dataclass
class Context:
    app: str = ""
    category: str = "other"
    before_cursor: str = ""
    after_cursor: str = ""
    names: list[str] = field(default_factory=list)
    is_terminal: bool = False
    agent: str = ""                                   # coding agent / IDE ("Claude Code", "Cursor", …)
    files: "filerefs.FileIndex | None" = None         # its project files, for @-mentions


@dataclass
class Result:
    text: str
    submit: bool = False
    used_llm: bool = False
    guard_reason: str = ""
    llm_seconds: float = 0.0


_SENTENCE_END = re.compile(r"[.!?…:\n]\s*$")


def _continue_mid_sentence(text: str, ctx: Context, keep_caps: set[str]) -> str:
    before = ctx.before_cursor
    if not text or not before.strip():
        return text
    if not _SENTENCE_END.search(before):
        first = re.match(r"[\wÄÖÜäöüß'-]+", text)
        word = first.group(0) if first else ""
        # German nouns stay capitalized: only function words capitalized purely for sentence start
        # are lowered, and never names/dictionary terms.
        if word and word not in keep_caps and word[0].isupper() and word.lower() in _COMMON_SENTENCE_STARTERS:
            text = word[0].lower() + text[1:]
    if before and not before[-1].isspace() and before[-1] not in "([{\"'„“-/" and text[0] not in ".,;:!?)":
        text = " " + text
    return text


# Function words that are only capitalized because they started the dictation.
_COMMON_SENTENCE_STARTERS = {
    "und", "aber", "oder", "dass", "weil", "wenn", "ob", "denn", "also", "dann", "ich", "du", "wir", "ihr", "sie",
    "er", "es", "man", "der", "die", "das", "den", "dem", "ein", "eine", "einen", "mit", "für", "auf", "in", "an",
    "zu", "von", "bei", "nach", "noch", "auch", "nur", "schon", "doch", "so", "wie", "was", "wo", "wann", "warum",
    "and", "but", "or", "because", "if", "so", "then", "the", "a", "an", "with", "for", "on", "in", "at", "to",
    "of", "we", "you", "they", "he", "she", "it", "that", "which", "who", "what", "when", "also", "just", "maybe",
}


def _messenger_period(text: str, style: str) -> str:
    """Wispr drops the trailing period for short messages in casual styles."""
    if style in ("casual", "very_casual") and text.endswith(".") and not text.endswith("..."):
        sentences = re.findall(r"[.!?]+(?:\s|$)", text)
        if len(sentences) <= 2 and "\n" not in text:
            return text[:-1]
    return text


def bullet_for(setting: str, category: str) -> str:
    """'auto': real bullets in chats/e-mail, Markdown dashes in editors, terminals and AI chats."""
    if setting in ("-", "•", "*"):
        return setting
    return "•" if category in ("personal", "work", "email") else "-"


async def process(raw: str, *, llm: Ollama | None, model: str, level: str, style: str, ctx: Context,
                  terms: list[dictionary.Term], snips: list[snippets.Snippet], timeout: float = 6.0,
                  formatting: bool = True, bullet: str = "auto", file_format: str = "@") -> Result:
    text, submit = rules.extract_submit(raw.strip())
    if not text:
        return Result("", submit)
    if level == "none":
        return Result(dictionary.correct(text, terms), submit)

    language = lang.detect(text)
    lex = lexicon.get()
    known_extra = {t.term for t in terms} | {w for t in terms for w in t.term.split()} | set(ctx.names)
    unknown = lex.unknown_words(text, known_extra) if lex.ready.is_set() else []
    if unknown:
        text = dictionary.correct(text, terms, set(unknown))
        unknown = [u for u in unknown if u in text]
    text, file_map = filerefs.protect(text, ctx.files, file_format, lex.known if lex.ready.is_set() else None)
    text, snip_map = snippets.protect(text, snips)
    text = rules.protect_breaks(text)
    res = Result("", submit)
    if llm is None or (level == "light" and not rules.needs_llm(text)):
        out = rules.light_clean(text)
    else:
        msgs = prompts.cleanup_messages(
            text, style=style, level=level, app=ctx.app, category=ctx.category,
            before_cursor=ctx.before_cursor, names=ctx.names, glossary=dictionary.glossary(terms),
            language=language, formatting=formatting,  # unknown-word hints confuse small models (tested)
        )
        try:
            r = await llm.chat(model, msgs, max_tokens=max(64, int(len(text.split()) * 3.5) + 40), timeout=timeout)
            res.used_llm, res.llm_seconds = True, r.seconds
            out = rules.keep_anglicisms(text, guard.strip_wrapping(r.text))
            ok, why = guard.check(text, out, level)
            res.guard_reason = why
            if not ok:
                log.warning("LLM output rejected (%s): %r -> %r", why, text, out)
                out = rules.light_clean(text, english=language == "English")
        except Exception as e:  # LLM down/slow: never lose the dictation
            log.warning("LLM cleanup failed: %s", e)
            res.guard_reason = f"error: {e}"
            out = rules.light_clean(text, english=language == "English")

    out = dictionary.correct(out, terms, set(unknown))
    out = rules.normalize_lines(rules.restore_placeholders(out))
    if formatting:
        out = rules.set_bullets(rules.format_lists(out), bullet_for(bullet, ctx.category))
    if language == "German":
        out = lex.normalize_anglicisms(out)
    out = filerefs.expand(out, file_map)
    if style == "very_casual":
        out = out.lower()
    if ctx.category in ("personal", "work"):
        out = _messenger_period(out, style)
    out = snippets.expand(out, snip_map)
    keep = {t.term.split()[0] for t in terms} | set(ctx.names)
    out = _continue_mid_sentence(out, ctx, keep)
    res.text = out
    return res
