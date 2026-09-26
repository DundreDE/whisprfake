"""Hyprland integration via hyprctl (Hyprland 0.56+ with Lua config: `hyprctl eval`)."""

from __future__ import annotations

import asyncio
import json
import logging

from .categories import AppInfo

log = logging.getLogger(__name__)


async def _run(*argv: str, stdin: bytes | None = None, timeout: float = 2.0) -> tuple[int, bytes]:
    p = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(p.communicate(stdin), timeout)
    except TimeoutError:
        p.kill()
        return -1, b""
    return p.returncode or 0, out


async def active_window() -> tuple[AppInfo, bool]:
    """Returns (app, is_terminal_by_hyprland_tag)."""
    rc, out = await _run("hyprctl", "activewindow", "-j")
    if rc != 0 or not out.strip().startswith(b"{"):
        return AppInfo(), False
    j = json.loads(out)
    tags = [t.rstrip("*") for t in j.get("tags", [])]
    return AppInfo(wm_class=j.get("class", ""), title=j.get("title", ""), pid=int(j.get("pid", 0))), "terminal" in tags


async def lua(code: str) -> bool:
    rc, out = await _run("hyprctl", "eval", code)
    return rc == 0 and out.strip().startswith(b"ok")


async def send_shortcut(mods: str, key: str) -> bool:
    """Send a key chord to the focused surface, same technique as Omarchy's universal paste
    (explicit mods, down/up split to avoid stuck synthetic keys)."""
    m, k = json.dumps(mods), json.dumps(key)
    return await lua(
        f'hl.dispatch(hl.dsp.send_key_state({{ mods = {m}, key = {k}, state = "down" }})); '
        f'hl.timer(function() hl.dispatch(hl.dsp.send_key_state({{ mods = {m}, key = {k}, state = "up" }})) end, '
        f'{{ timeout = 40, type = "oneshot" }})'
    )


async def grab_escape(on: bool) -> None:
    """While recording, bind ESC to a no-op so it cancels only the dictation and doesn't also reach the
    focused app. The daemon still sees the key through evdev."""
    if on:
        await lua('if not _G.whisprfake_esc then _G.whisprfake_esc = hl.bind("ESCAPE", hl.dsp.exec_cmd("true"), '
                  '{ description = "whisprfake: cancel dictation" }) end')
    else:
        await lua("if _G.whisprfake_esc then _G.whisprfake_esc:unbind(); _G.whisprfake_esc = nil end")


async def notify(text: str, ms: int = 3000) -> None:
    await _run("notify-send", "-a", "whisprfake", "-t", str(ms), "whisprfake", text)
