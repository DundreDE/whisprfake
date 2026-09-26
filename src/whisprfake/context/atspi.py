"""Context awareness via AT-SPI: text around the caret, current selection, names visible nearby and
the browser URL. A background GLib thread tracks focus events so a snapshot at dictation time is just
a few cheap calls on the already-known focused object (Wispr: skip context if it isn't fast)."""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

try:
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi, GLib
except Exception:  # pragma: no cover - AT-SPI missing
    Atspi = None


@dataclass
class TextContext:
    before: str = ""
    after: str = ""
    selection: str = ""
    names: list[str] = field(default_factory=list)
    url: str = ""
    pid: int = 0
    password: bool = False
    acc: object = None  # the focused accessible (for auto-learn re-reads)


_NAME = re.compile(r"\b[A-ZÄÖÜ][a-zäöüß]{1,20}(?:\s[A-ZÄÖÜ][a-zäöüß]{1,20})?\b")
_STOP = {"Der", "Die", "Das", "Ich", "Wir", "Sie", "Und", "Aber", "The", "This", "That", "And", "But", "You", "Hello",
         "Hallo", "Danke", "Thanks", "Re", "Fwd", "Von", "An", "Betreff", "Subject", "From", "To", "Datei", "File"}


def set_a11y_enabled(on: bool) -> None:
    """Toggle org.a11y.Status.IsEnabled so Chromium/Electron/GTK4 expose their accessibility trees."""
    import subprocess

    subprocess.run(["busctl", "--user", "set-property", "org.a11y.Bus", "/org/a11y/bus", "org.a11y.Status",
                    "IsEnabled", "b", "true" if on else "false"], capture_output=True, timeout=3)


class FocusTracker:
    def __init__(self):
        self.focused = None
        self.focused_at = 0.0
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.loop = None

    def start(self) -> None:
        if Atspi is None or self.thread:
            return
        self.thread = threading.Thread(target=self._run, name="atspi", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        Atspi.init()
        self.listener = Atspi.EventListener.new(self._on_event)
        self.listener.register("object:state-changed:focused")
        self.listener.register("object:text-caret-moved")
        self.loop = GLib.MainLoop()
        self.loop.run()

    def _on_event(self, ev) -> None:
        try:
            if ev.type.startswith("object:state-changed:focused") and not ev.detail1:
                return
            with self.lock:
                self.focused, self.focused_at = ev.source, time.monotonic()
        except Exception:
            pass

    def snapshot(self, pid: int = 0, budget_s: float = 0.08) -> TextContext:
        """Read context from the focused accessible. Must stay under `budget_s`."""
        t0 = time.monotonic()
        ctx = TextContext()
        with self.lock:
            acc = self.focused
        if acc is None:
            return ctx
        try:
            ctx.pid = acc.get_process_id()
            ctx.acc = acc
            if pid and ctx.pid != pid:
                return TextContext()  # stale focus from another app
            if acc.get_role() == Atspi.Role.PASSWORD_TEXT:
                ctx.password = True
                return ctx
            text = acc.get_text_iface() if hasattr(acc, "get_text_iface") else None
            if text is not None:
                n = Atspi.Text.get_character_count(acc)
                caret = Atspi.Text.get_caret_offset(acc)
                caret = n if caret < 0 else caret
                ctx.before = Atspi.Text.get_text(acc, max(0, caret - 1500), caret) or ""
                ctx.after = Atspi.Text.get_text(acc, caret, min(n, caret + 300)) or ""
                if Atspi.Text.get_n_selections(acc) > 0:
                    r = Atspi.Text.get_selection(acc, 0)
                    if r.end_offset > r.start_offset:
                        ctx.selection = Atspi.Text.get_text(acc, r.start_offset, r.end_offset) or ""
            if time.monotonic() - t0 < budget_s:
                ctx.names, ctx.url = self._nearby(acc, t0, budget_s)
        except Exception as e:
            log.debug("atspi snapshot failed: %s", e)
        return ctx

    def _nearby(self, acc, t0: float, budget_s: float) -> tuple[list[str], str]:
        """Walk up to the window and scan a bounded number of nodes for names and a URL entry."""
        names: dict[str, None] = {}
        url = ""
        node = acc
        for _ in range(12):
            parent = node.get_parent()
            if parent is None or parent.get_role() in (Atspi.Role.APPLICATION,):
                break
            node = parent
        queue, seen = [node], 0
        while queue and seen < 400 and time.monotonic() - t0 < budget_s:
            n = queue.pop(0)
            seen += 1
            try:
                role = n.get_role()
                name = n.get_name() or ""
                if role in (Atspi.Role.ENTRY,) and not url:
                    try:
                        v = Atspi.Text.get_text(n, 0, 300) or ""
                        if re.match(r"^(https?://|[\w-]+\.[\w.-]+/)", v):
                            url = v
                    except Exception:
                        pass
                if name and role in (Atspi.Role.LABEL, Atspi.Role.LINK, Atspi.Role.HEADING, Atspi.Role.LIST_ITEM,
                                     Atspi.Role.PUSH_BUTTON, Atspi.Role.STATIC, Atspi.Role.TABLE_CELL):
                    for m in _NAME.findall(name):
                        if m.split()[0] not in _STOP:
                            names[m] = None
                if role == Atspi.Role.DOCUMENT_WEB and not url:
                    try:
                        doc = n.get_document_iface()
                        url = doc.get_document_attribute_value("DocURL") or ""
                    except Exception:
                        pass
                for i in range(min(n.get_child_count(), 60)):
                    c = n.get_child_at_index(i)
                    if c is not None:
                        queue.append(c)
            except Exception:
                continue
        return list(names)[:40], url


def read_text(acc, limit: int = 20000) -> str:
    """Current full text of an accessible text field ('' if gone)."""
    try:
        n = Atspi.Text.get_character_count(acc)
        return Atspi.Text.get_text(acc, max(0, n - limit), n) or ""
    except Exception:
        return ""
