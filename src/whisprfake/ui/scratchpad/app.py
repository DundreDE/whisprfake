"""Scratchpad: a floating notepad with tabs, autosave and version history (Wispr Flow's Scratchpad).
Dictate straight into it like into any other app."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ..client import DaemonError, call  # noqa: E402
from ..hub.widgets import when  # noqa: E402

APP_ID = "dev.whisprfake.Scratchpad"


class NoteTab(Gtk.ScrolledWindow):
    def __init__(self, win, note: dict | None):
        super().__init__(vexpand=True)
        self.win = win
        self.note_id = note["id"] if note else None
        self.view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=14, bottom_margin=14, left_margin=16,
                                 right_margin=16)
        self.view.add_css_class("scratch")
        self.buf = self.view.get_buffer()
        if note:
            self.buf.set_text(note.get("body") or "")
        self.set_child(self.view)
        self._timer = 0
        self.buf.connect("changed", self._changed)

    def text(self) -> str:
        return self.buf.get_text(self.buf.get_start_iter(), self.buf.get_end_iter(), False)

    def title(self) -> str:
        first = (self.text().strip().splitlines() or [""])[0][:28]
        return first or "Neue Notiz"

    def _changed(self, *_):
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(700, self.save)
        self.win.retitle(self)

    def save(self) -> bool:
        self._timer = 0
        body = self.text()
        if self.note_id is None and not body.strip():
            return False
        try:
            self.note_id = call("notes.save", {"id": self.note_id, "body": body})
            self.win.saved.set_label("gespeichert")
        except DaemonError as e:
            self.win.saved.set_label(str(e))
        return False


class ScratchWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Scratchpad", default_width=560, default_height=460)
        css = Gtk.CssProvider()
        css.load_from_string(".scratch { font-size: 15px; background: transparent; }")
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.tabs = Adw.TabView()
        self.tabs.connect("close-page", self._close_page)
        bar = Adw.TabBar(view=self.tabs, autohide=False)
        hb = Adw.HeaderBar()
        new = Gtk.Button(icon_name="tab-new-symbolic", tooltip_text="Neue Notiz (Ctrl+T)")
        new.connect("clicked", lambda *_: self.open_note(None))
        hb.pack_start(new)
        hist = Gtk.MenuButton(icon_name="document-open-recent-symbolic", tooltip_text="Versionen")
        self.hist_pop = Gtk.Popover()
        hist.set_popover(self.hist_pop)
        hist.connect("notify::active", self._fill_history)
        hb.pack_end(hist)
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Alles kopieren")
        copy.connect("clicked", self._copy)
        hb.pack_end(copy)
        self.saved = Gtk.Label()
        self.saved.add_css_class("dim-label")
        self.saved.add_css_class("caption")
        hb.pack_end(self.saved)
        tv = Adw.ToolbarView(content=self.tabs)
        tv.add_top_bar(hb)
        tv.add_top_bar(bar)
        self.set_content(tv)
        sc = Gtk.ShortcutController()
        sc.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("<Control>t"),
                                     action=Gtk.CallbackAction.new(lambda *_: (self.open_note(None), True)[1])))
        sc.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("Escape"),
                                     action=Gtk.CallbackAction.new(lambda *_: (self.close(), True)[1])))
        self.add_controller(sc)
        try:
            notes = call("notes.list")[:8]
        except DaemonError:
            notes = []
        for n in reversed(notes):
            self.open_note(n["id"], select=False)
        if not notes:
            self.open_note(None)

    def open_note(self, note_id: int | None, select: bool = True) -> None:
        for i in range(self.tabs.get_n_pages()):
            pg = self.tabs.get_nth_page(i)
            if note_id is not None and pg.get_child().note_id == note_id:
                self.tabs.set_selected_page(pg)
                return
        note = None
        if note_id is not None:
            try:
                note = call("notes.get", {"id": note_id})
            except DaemonError:
                pass
        tab = NoteTab(self, note)
        page = self.tabs.add_page(tab, None)
        page.set_title(tab.title())
        if select:
            self.tabs.set_selected_page(page)
            tab.view.grab_focus()

    def retitle(self, tab: NoteTab) -> None:
        self.tabs.get_page(tab).set_title(tab.title())
        self.saved.set_label("…")

    def current(self) -> NoteTab | None:
        pg = self.tabs.get_selected_page()
        return pg.get_child() if pg else None

    def _close_page(self, view, page):
        page.get_child().save()
        view.close_page_finish(page, True)
        if view.get_n_pages() == 0:
            self.open_note(None)
        return True

    def _copy(self, *_):
        if (t := self.current()) is not None:
            self.get_clipboard().set(t.text())
            self.saved.set_label("kopiert")

    def _fill_history(self, btn, _p):
        if not btn.get_active():
            return
        t = self.current()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6)
        versions = []
        if t and t.note_id:
            try:
                versions = call("notes.versions", {"id": t.note_id})
            except DaemonError:
                pass
        if not versions:
            box.append(Gtk.Label(label="Noch keine Versionen", margin_start=12, margin_end=12))
        for v in versions[:20]:
            b = Gtk.Button(label=f"{when(v['ts'])} – {(v['body'] or '').strip()[:40]}")
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, body=v["body"]: (t.buf.set_text(body), self.hist_pop.popdown()))
            box.append(b)
        self.hist_pop.set_child(box)

    def do_close_request(self):
        for i in range(self.tabs.get_n_pages()):
            self.tabs.get_nth_page(i).get_child().save()
        return False


class ScratchApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]
        win = self.props.active_window or ScratchWindow(self)
        if "--note" in args:
            win.open_note(int(args[args.index("--note") + 1]))
        elif "--toggle" in args and win.is_visible() and win.is_active():
            win.close()
            return 0
        win.present()
        return 0


def main(argv: list[str] | None = None) -> None:
    ScratchApp().run([sys.argv[0], *(argv or [])])
