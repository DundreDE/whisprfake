"""Insert text into the focused app like Wispr does: via the clipboard + paste shortcut, then restore
the previous clipboard. Falls back to typing with wtype."""

from __future__ import annotations

import asyncio
import logging

from ..context import hypr

log = logging.getLogger(__name__)

TEXT_TYPES = ("text/plain;charset=utf-8", "text/plain", "UTF8_STRING", "STRING", "TEXT")


async def _run(*argv: str, stdin: bytes | None = None, timeout: float = 2.0) -> tuple[int, bytes]:
    return await hypr._run(*argv, stdin=stdin, timeout=timeout)


async def read_clipboard(primary: bool = False) -> tuple[str, bytes] | None:
    flag = ["--primary"] if primary else []
    rc, types = await _run("wl-paste", *flag, "--list-types")
    if rc != 0:
        return None
    avail = types.decode(errors="replace").split()
    if not avail:
        return None
    mime = next((t for t in TEXT_TYPES if t in avail), avail[0])
    rc, data = await _run("wl-paste", *flag, "--no-newline", "--type", mime, timeout=3.0)
    return (mime, data) if rc == 0 else None


async def write_clipboard(data: bytes, mime: str = "text/plain;charset=utf-8", sensitive: bool = True) -> bool:
    argv = ["wl-copy", "--type", mime] + (["--sensitive"] if sensitive else [])
    rc, _ = await _run(*argv, stdin=data)
    return rc == 0


async def clear_clipboard() -> None:
    await _run("wl-copy", "--clear")


async def paste_text(text: str, is_terminal: bool, submit: bool = False, restore: bool = True) -> bool:
    if not text:
        if submit:
            await hypr.send_shortcut("", "Return")
        return True
    saved = await read_clipboard() if restore else None
    ok = await write_clipboard(text.encode())
    if ok:
        await asyncio.sleep(0.03)
        mods, key = ("SHIFT", "Insert") if is_terminal else ("CTRL", "V")
        ok = await hypr.send_shortcut(mods, key)
    if not ok:
        log.warning("clipboard paste failed, typing with wtype")
        rc, _ = await _run("wtype", "-", stdin=text.encode(), timeout=30)
        ok = rc == 0
    if submit:
        await asyncio.sleep(0.08)
        await hypr.send_shortcut("", "Return")
    if restore:
        await asyncio.sleep(0.35)  # let the app fetch the paste before swapping the clipboard back
        if saved:
            await write_clipboard(saved[1], saved[0], sensitive=False)
        else:
            await clear_clipboard()
    return ok


async def copy_only(text: str) -> None:
    await write_clipboard(text.encode(), sensitive=False)
