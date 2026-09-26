"""Spoken file names → @-mentions for coding agents (Wispr Flow's "file tagging", for every agent).

"schau dir mal auth punkt ts an" / "check the readme" / "in der user service datei" → "@src/lib/auth.ts",
"@README.md", "@src/services/userService.ts". File names come from the agent's project (git ls-files, or a
bounded directory walk), so only files that actually exist are tagged.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target", ".next", ".cache",
             ".idea", ".vscode", "vendor", ".mypy_cache", ".pytest_cache", "coverage", ".turbo", ".svelte-kit"}
MAX_FILES = 20000
DOT_WORDS = {"dot", "punkt", "point"}
FILE_WORDS = {"datei", "file", "files", "dateien", "komponente", "component", "modul", "module", "skript", "script",
              "klasse", "class", "config", "konfig", "test", "tests", "seite", "page", "hook", "route", "schema"}
# Everyday dev words that are also file names: tagged only with an extension or a "Datei/file" next to them.
COMMON_STEMS = {"config", "auth", "index", "main", "utils", "util", "test", "tests", "api", "app", "server", "client",
                "types", "type", "schema", "setup", "docs", "doc", "src", "lib", "style", "styles", "theme", "store",
                "model", "models", "service", "services", "routes", "router", "layout", "page", "home", "login",
                "user", "users", "data", "database", "helpers", "helper", "constants", "settings", "hooks", "init",
                "build", "package", "readme2", "license", "makefile", "docker", "env", "cache", "logger", "db"}
_TOKEN = re.compile(r"[A-Za-zÄÖÜäöüß0-9_]+(?:[.\-][A-Za-z0-9_]+)*")


def _split_ident(stem: str) -> list[str]:
    """userService / user-service / user_service / UserService → ['user', 'service']"""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", stem)
    return [p for p in re.split(r"[\s_\-.]+", s.lower()) if p]


@dataclass
class FileIndex:
    root: Path
    files: list[str] = field(default_factory=list)       # relative paths
    by_full: dict[str, list[str]] = field(default_factory=dict)   # "userservice.ts" -> paths
    by_stem: dict[str, list[str]] = field(default_factory=dict)   # "userservice" -> paths
    exts: set[str] = field(default_factory=set)
    built: float = 0.0

    def build(self) -> "FileIndex":
        files: list[str] = []
        try:
            r = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=self.root,
                               capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                files = [f for f in r.stdout.splitlines() if f][:MAX_FILES]
        except (OSError, subprocess.TimeoutExpired):
            pass
        if not files:
            for dirpath, dirnames, filenames in os.walk(self.root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
                rel = os.path.relpath(dirpath, self.root)
                files += [f if rel == "." else f"{rel}/{f}" for f in filenames]
                if len(files) > MAX_FILES:
                    break
        self.files = files
        for f in files:
            name = os.path.basename(f)
            if "." in name.strip("."):
                stem, ext = name.rsplit(".", 1)
            else:
                stem, ext = name, ""
            compact = "".join(_split_ident(stem))
            if not compact:
                continue
            if ext:
                self.by_full.setdefault(f"{compact}.{ext.lower()}", []).append(f)
                self.exts.add(ext.lower())
            self.by_stem.setdefault(compact, []).append(f)
        self.built = time.time()
        return self

    def names_for_asr(self, limit: int = 120) -> list[str]:
        """Most recently modified file names – spelling hints for the speech recognizer."""
        if getattr(self, "_asr_names", None) is not None:
            return self._asr_names[:limit]
        scored = []
        for f in self.files[:5000]:
            try:
                scored.append(((self.root / f).stat().st_mtime, os.path.basename(f)))
            except OSError:
                pass
        scored.sort(reverse=True)
        self._asr_names = list(dict.fromkeys(n for _, n in scored))
        return self._asr_names[:limit]


_CACHE: dict[Path, FileIndex] = {}
_LOCK = threading.Lock()


def index_for(root: Path, max_age: float = 30.0) -> FileIndex:
    with _LOCK:
        idx = _CACHE.get(root)
        if idx is None or time.time() - idx.built > max_age:
            idx = FileIndex(root).build()
            _CACHE[root] = idx
        return idx


def _pick(paths: list[str], context_words: set[str], root: Path) -> str:
    """Several files share the name: prefer one whose directories were mentioned, then shallow, then recent."""
    def score(p: str):
        dirs = set(_split_ident(" ".join(Path(p).parent.parts)))
        try:
            mtime = (root / p).stat().st_mtime
        except OSError:
            mtime = 0
        return (-len(dirs & context_words), p.count("/"), -mtime)
    return sorted(paths, key=score)[0]


def find_mentions(text: str, idx: FileIndex, is_known_word=None) -> list[tuple[int, int, str]]:
    """Return (start, end, relative path) spans in `text` that name a project file."""
    tokens = list(_TOKEN.finditer(text))
    words_lower = {t.group(0).lower() for t in tokens}
    out: list[tuple[int, int, str]] = []
    i = 0
    while i < len(tokens):
        best = None
        for n in range(min(6, len(tokens) - i), 0, -1):
            span = tokens[i:i + n]
            # tokens must be separated by spaces/hyphens/dots only (no punctuation in between)
            if any(not re.fullmatch(r"[\s\-.]*", text[span[k].end():span[k + 1].start()]) for k in range(n - 1)):
                continue
            parts = [t.group(0).lower() for t in span]
            # "auth punkt ts" / "auth dot ts" / "auth.ts" / "auth ts"
            if len(parts) >= 3 and parts[-2] in DOT_WORDS and parts[-1] in idx.exts:
                full = "".join("".join(_split_ident(p)) for p in parts[:-2]) + "." + parts[-1]
            elif "." in parts[-1] and parts[-1].rsplit(".", 1)[1] in idx.exts:
                stem, ext = parts[-1].rsplit(".", 1)
                full = "".join("".join(_split_ident(p)) for p in [*parts[:-1], stem]) + "." + ext
            elif len(parts) >= 2 and parts[-1] in idx.exts:  # "auth ts" (the file must exist, so this is safe)
                full = "".join("".join(_split_ident(p)) for p in parts[:-1]) + "." + parts[-1]
            else:
                full = None
            if full and full in idx.by_full:
                best = (span[0].start(), span[-1].end(), idx.by_full[full])
                break
            # stem only ("readme", "user service") – needs a distinctive stem or a "Datei/file" next to it
            stem = "".join("".join(_split_ident(p)) for p in parts)
            if stem in idx.by_stem and len(stem) >= 4:
                nxt = tokens[i + n].group(0).lower() if i + n < len(tokens) else ""
                prv = tokens[i - 1].group(0).lower() if i > 0 else ""
                distinctive = stem not in COMMON_STEMS and (
                    n >= 2 or (is_known_word is not None and not is_known_word(span[0].group(0))))
                if distinctive or nxt in FILE_WORDS or prv in FILE_WORDS:
                    best = (span[0].start(), span[-1].end(), idx.by_stem[stem])
                    break
        if best:
            start, end, paths = best
            out.append((start, end, _pick(paths, words_lower, idx.root)))
            while i < len(tokens) and tokens[i].start() < end:
                i += 1
        else:
            i += 1
    return out


def protect(text: str, idx: FileIndex | None, fmt: str = "@", is_known_word=None) -> tuple[str, dict[str, str]]:
    """Replace file mentions with ⟦F<n>⟧ placeholders (so the LLM can't mangle them)."""
    if idx is None:
        return text, {}
    mapping: dict[str, str] = {}
    for n, (s, e, path) in enumerate(reversed(find_mentions(text, idx, is_known_word))):
        ph = f"⟦F{n}⟧"
        mapping[ph] = f"@{path}" if fmt == "@" else f"`{path}`"
        text = text[:s] + ph + text[e:]
    return text, mapping


def expand(text: str, mapping: dict[str, str]) -> str:
    # German compounds glue words onto the name ("⟦F0⟧-Datei"); a mention must stay a separate token
    text = re.sub(r"(⟦F\d+⟧)-(?=\w)", r"\1 ", text)
    for ph, val in mapping.items():
        text = text.replace(ph, val)
    return text
