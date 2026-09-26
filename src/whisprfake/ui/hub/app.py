"""whisprfake Hub – the main app window (like Wispr Flow's Hub)."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ..client import subscribe  # noqa: E402
from .pages import (DictionaryPage, HistoryPage, HomePage, MeetingsPage, NotesPage, SnippetsPage,  # noqa: E402
                    StylesPage, TransformsPage)
from .settings import SettingsPage  # noqa: E402

APP_ID = "dev.whisprfake.Hub"


class HubWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="whisprfake", default_width=1100, default_height=760)
        self.toasts = Adw.ToastOverlay()
        self.pages = [cls(self) for cls in (HomePage, HistoryPage, DictionaryPage, SnippetsPage, StylesPage,
                                            TransformsPage, NotesPage, MeetingsPage, SettingsPage)]
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        for p in self.pages:
            self.stack.add_named(p.widget, p.title)

        sidebar = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        sidebar.add_css_class("navigation-sidebar")
        for p in self.pages:
            row = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=6)
            row.append(Gtk.Image(icon_name=p.icon))
            row.append(Gtk.Label(label=p.title, xalign=0))
            sidebar.append(row)
        sidebar.connect("row-selected", self._select)
        self.sidebar = sidebar

        # brand
        brand = Gtk.Box(spacing=10, margin_start=14, margin_end=14, margin_top=4, margin_bottom=14)
        logo = Gtk.Box(valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
        logo.set_size_request(34, 34)
        logo.add_css_class("wf-brand-icon")
        logo.append(Gtk.Image(icon_name="audio-input-microphone-symbolic", pixel_size=18, hexpand=True,
                              halign=Gtk.Align.CENTER))
        brand.append(logo)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        t1 = Gtk.Label(label="whisprfake", xalign=0)
        t1.add_css_class("wf-brand-title")
        t2 = Gtk.Label(label="lokal · privat · schnell", xalign=0)
        t2.add_css_class("wf-brand-sub")
        names.append(t1)
        names.append(t2)
        brand.append(names)

        # status pill at the bottom
        status_box = Gtk.Box(spacing=8, margin_start=18, margin_bottom=14, margin_top=8)
        self.dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.dot.add_css_class("wf-status-dot")
        self.status = Gtk.Label(xalign=0, label="verbinde …")
        self.status.add_css_class("caption")
        self.status.add_css_class("dim-label")
        status_box.append(self.dot)
        status_box.append(self.status)

        side_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        side_box.append(brand)
        side_box.append(Gtk.ScrolledWindow(child=sidebar, vexpand=True))
        side_box.append(status_box)
        side_tv = Adw.ToolbarView(content=side_box)
        hb = Adw.HeaderBar(show_title=False)
        side_tv.add_top_bar(hb)

        self.content_title = Adw.WindowTitle()
        content_tv = Adw.ToolbarView(content=self.stack)
        chb = Adw.HeaderBar(title_widget=self.content_title)
        content_tv.add_top_bar(chb)

        split = Adw.NavigationSplitView(
            sidebar=Adw.NavigationPage(child=side_tv, title="whisprfake"),
            content=Adw.NavigationPage(child=content_tv, title="Inhalt"),
            min_sidebar_width=210, max_sidebar_width=240,
        )
        self.toasts.set_child(split)
        self.set_content(self.toasts)
        sidebar.select_row(sidebar.get_row_at_index(0))
        subscribe(self._event)

    def _select(self, _lb, row):
        if row is None:
            return
        p = self.pages[row.get_index()]
        self.stack.set_visible_child_name(p.title)
        self.content_title.set_title(p.title)
        p.refresh()

    def current(self):
        return self.pages[self.sidebar.get_selected_row().get_index()]

    def toast(self, msg: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(msg), timeout=3))

    def _event(self, ev: dict) -> None:
        e = ev.get("event")
        if e == "state":
            labels = {"idle": "bereit", "recording": "nimmt auf …", "processing": "verarbeitet …"}
            self.status.set_label(labels.get(ev["state"], ev["state"]))
            for c in ("ok", "rec"):
                self.dot.remove_css_class(c)
            self.dot.add_css_class("ok" if ev["state"] == "idle" else "rec")
        elif e == "disconnected":
            self.status.set_label("Dienst nicht erreichbar")
            for c in ("ok", "rec"):
                self.dot.remove_css_class(c)
        elif e in ("inserted", "notes_changed", "meeting_changed"):
            if self.current().title in ("Übersicht", "Verlauf", "Notizen", "Meetings"):
                self.current().refresh()


    def show_page(self, name: str) -> None:
        for i, p in enumerate(self.pages):
            if p.title.lower() == name.lower():
                self.sidebar.select_row(self.sidebar.get_row_at_index(i))


class HubApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)

    def do_startup(self):
        Adw.Application.do_startup(self)
        from ..theme import apply

        apply()

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]
        win = self.props.active_window or HubWindow(self)
        if "--page" in args:
            win.show_page(args[args.index("--page") + 1])
        win.present()
        return 0


def main(argv: list[str] | None = None) -> None:
    HubApp().run([sys.argv[0], *(argv or [])])
