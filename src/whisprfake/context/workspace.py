"""Coding-agent detection: which agent/IDE you're talking to and which project folder it works in.

* Terminals: walk the process tree below the terminal window and look for claude / codex / gemini / aider /
  opencode / … ; the agent process's working directory is the project.
* IDEs (VS Code, Cursor, Windsurf, VSCodium, Zed): the folder name from the window title, resolved to a
  path via the editor's own storage.json (recent/open folders) or a shallow search in common project dirs.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from .categories import AppInfo

TERMINAL_AGENTS = {
    "claude": "Claude Code", "codex": "Codex", "gemini": "Gemini CLI", "aider": "aider", "opencode": "opencode",
    "crush": "Crush", "amp": "Amp", "goose": "Goose", "cursor-agent": "Cursor Agent", "qwen": "Qwen Code",
    "copilot": "Copilot CLI", "kiro": "Kiro",
}
IDES = {  # window class prefix -> (name, config dir name)
    "code": ("VS Code", "Code"), "code-oss": ("VS Code", "Code - OSS"), "vscodium": ("VSCodium", "VSCodium"),
    "cursor": ("Cursor", "Cursor"), "windsurf": ("Windsurf", "Windsurf"), "dev.zed.zed": ("Zed", None),
    "zed": ("Zed", None),
}
PROJECT_ROOTS = [Path.home() / d for d in ("DEV", "dev", "Projects", "projects", "code", "src", "git", "repos", "work")]


@dataclass
class Workspace:
    agent: str = ""        # "Claude Code", "Cursor", … ("" = no coding agent/IDE)
    root: Path | None = None


def _children(pid: int) -> list[int]:
    out: list[int] = []
    try:
        for tid in os.listdir(f"/proc/{pid}/task"):
            try:
                out += [int(x) for x in Path(f"/proc/{pid}/task/{tid}/children").read_text().split()]
            except OSError:
                pass
    except OSError:
        pass
    return out


def _comm(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return ""


def _cmdline(pid: int) -> list[str]:
    try:
        return [a for a in Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0") if a]
    except OSError:
        return []


def _agent_in_tree(pid: int, depth: int = 0) -> tuple[str, int] | None:
    if depth > 6:
        return None
    for c in _children(pid):
        comm = _comm(c).lower()
        name = TERMINAL_AGENTS.get(comm)
        if not name and comm in ("node", "bun", "python", "python3"):
            # agents started via node/python: look at the script name
            for arg in _cmdline(c)[1:3]:
                base = os.path.basename(arg).lower()
                for key, label in TERMINAL_AGENTS.items():
                    if base == key or base.startswith(key + "-") or f"/{key}/" in arg.lower():
                        name = label
                        break
                if name:
                    break
        if name:
            return name, c
        if found := _agent_in_tree(c, depth + 1):
            return found
    return None


def _cwd(pid: int) -> Path | None:
    try:
        return Path(os.readlink(f"/proc/{pid}/cwd"))
    except OSError:
        return None


def _git_root(p: Path) -> Path:
    for q in [p, *p.parents]:
        if (q / ".git").exists():
            return q
        if q == Path.home():
            break
    return p


def _ide_folder_name(title: str) -> str:
    # "file.ts - project - Visual Studio Code" / "project - Cursor" / "● file — project — Zed"
    parts = [x.strip(" ●") for x in re.split(r"\s+[-—–]\s+", title) if x.strip()]
    if len(parts) >= 3:
        return parts[-2]
    if len(parts) == 2:
        return parts[0]
    return ""


def _ide_known_folders(config_dir: str | None) -> list[Path]:
    if not config_dir:
        return []
    f = Path.home() / ".config" / config_dir / "User/globalStorage/storage.json"
    try:
        d = json.loads(f.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    uris: list[str] = []
    ws = d.get("windowsState", {})
    for w in [ws.get("lastActiveWindow", {}), *ws.get("openedWindows", [])]:
        if w.get("folder"):
            uris.append(w["folder"])
    for entry in (d.get("backupWorkspaces") or {}).get("folders", []):
        if isinstance(entry, dict) and entry.get("folderUri"):
            uris.append(entry["folderUri"])
    out = []
    for u in uris:
        p = urlparse(u)
        if p.scheme == "file":
            out.append(Path(unquote(p.path)))
    return out


_search_cache: dict[str, tuple[float, Path | None]] = {}


def _search_projects(name: str) -> Path | None:
    hit = _search_cache.get(name)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    found = None
    for root in PROJECT_ROOTS:
        if not root.is_dir():
            continue
        for dirpath, dirnames, _ in os.walk(root):
            depth = len(Path(dirpath).relative_to(root).parts)
            if name in dirnames:
                found = Path(dirpath) / name
                break
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("node_modules", "target", "dist")]
            if depth >= 2:
                dirnames[:] = []
        if found:
            break
    _search_cache[name] = (time.time(), found)
    return found


def detect(app: AppInfo, is_terminal: bool) -> Workspace:
    cls = app.wm_class.lower()
    if is_terminal and app.pid:
        found = _agent_in_tree(app.pid)
        if found:
            name, pid = found
            cwd = _cwd(pid)
            return Workspace(name, _git_root(cwd) if cwd else None)
        return Workspace()
    for prefix, (name, cfg) in IDES.items():
        if cls == prefix or cls.startswith(prefix + "-") or cls.startswith(prefix + "."):
            folder = _ide_folder_name(app.title)
            if not folder:
                return Workspace(name, None)
            for p in _ide_known_folders(cfg):
                if p.name == folder and p.is_dir():
                    return Workspace(name, p)
            return Workspace(name, _search_projects(folder))
    return Workspace()
