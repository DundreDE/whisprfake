"""Small shared GTK helpers for the Hub."""

from __future__ import annotations

import datetime as dt

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402


def clear(box: Gtk.Widget) -> None:
    """Remove all rows/children from a ListBox / PreferencesGroup / Box."""
    if isinstance(box, Adw.PreferencesGroup):
        for row in getattr(box, "_rows", []):
            box.remove(row)
        box._rows = []
        return
    child = box.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def add(group: Adw.PreferencesGroup, row: Gtk.Widget) -> Gtk.Widget:
    group.add(row)
    group._rows = getattr(group, "_rows", []) + [row]
    return row


def icon_button(icon: str, tooltip: str, cb, *args, css: str = "flat") -> Gtk.Button:
    b = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    b.add_css_class(css)
    b.connect("clicked", lambda *_: cb(*args))
    return b


def when(ts: float | None) -> str:
    if not ts:
        return ""
    d = dt.datetime.fromtimestamp(ts)
    today = dt.date.today()
    if d.date() == today:
        return d.strftime("heute %H:%M")
    if d.date() == today - dt.timedelta(days=1):
        return d.strftime("gestern %H:%M")
    return d.strftime("%d.%m.%Y %H:%M")


def esc(s: str | None) -> str:
    from gi.repository import GLib

    return GLib.markup_escape_text(s or "")


def page(title: str, *groups: Gtk.Widget) -> Adw.PreferencesPage:
    p = Adw.PreferencesPage(title=title)
    for g in groups:
        p.add(g)
    return p


def stat_card(value: str, label: str) -> tuple[Gtk.Widget, Gtk.Label]:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
    box.add_css_class("card")
    box.set_margin_top(4)
    v = Gtk.Label(label=value, margin_top=14, margin_start=12, margin_end=12)
    v.add_css_class("title-1")
    lab = Gtk.Label(label=label, margin_bottom=14)
    lab.add_css_class("dim-label")
    box.append(v)
    box.append(lab)
    return box, v
