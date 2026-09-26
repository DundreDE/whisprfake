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

        self.status = Gtk.Label(xalign=0, margin_start=12, margin_bottom=10, margin_top=6)
        self.status.add_css_class("caption")
        self.status.add_css_class("dim-label")
        side_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        side_box.append(Gtk.ScrolledWindow(child=sidebar, vexpand=True))
        side_box.append(self.status)
        side_tv = Adw.ToolbarView(content=side_box)
        hb = Adw.HeaderBar()
        title = Gtk.Label(label="whisprfake")
        title.add_css_class("heading")
        hb.set_title_widget(title)
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
            labels = {"idle": "● bereit", "recording": "● nimmt auf", "processing": "● verarbeitet"}
            self.status.set_label(labels.get(ev["state"], ev["state"]))
        elif e == "disconnected":
            self.status.set_label("○ Dienst nicht erreichbar")
        elif e in ("inserted", "notes_changed", "meeting_changed"):
            if self.current().title in ("Übersicht", "Verlauf", "Notizen", "Meetings"):
                self.current().refresh()


class HubApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        win = self.props.active_window or HubWindow(self)
        win.present()


def main() -> None:
    HubApp().run(sys.argv[:1])
