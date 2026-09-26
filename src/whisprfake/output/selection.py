"""Read the user's current text selection (for Command Mode)."""

from __future__ import annotations

import asyncio

from ..context import hypr
from . import inject


async def selected_text(prefer_atspi: str | None = None) -> str:
    """AT-SPI selection if we already have it, else the Wayland PRIMARY selection. PRIMARY can be stale
    (last thing ever selected), so the caller should prefer AT-SPI when available."""
    if prefer_atspi:
        return prefer_atspi
    got = await inject.read_clipboard(primary=True)
    if got and (got[0].startswith("text") or got[0] in inject.TEXT_TYPES):
        return got[1].decode(errors="replace")
    return ""


async def copy_selection_via_ctrl_c(is_terminal: bool) -> str:
    """Fallback: send Ctrl+C (Ctrl+Shift+C in terminals) and read the clipboard."""
    saved = await inject.read_clipboard()
    await inject.clear_clipboard()
    await hypr.send_shortcut("CTRL SHIFT" if is_terminal else "CTRL", "C")
    await asyncio.sleep(0.15)
    got = await inject.read_clipboard()
    if saved:
        await inject.write_clipboard(saved[1], saved[0], sensitive=False)
    return got[1].decode(errors="replace") if got else ""
