"""Make the GTK apps look like the rest of Omarchy: colours come from the active Omarchy theme
(~/.local/state/omarchy/current/theme/colors.toml), falling back to libadwaita's defaults."""

from __future__ import annotations

import tomllib
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

THEME = Path.home() / ".local/state/omarchy/current/theme/colors.toml"


def colors() -> dict[str, str]:
    try:
        return tomllib.loads(THEME.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _mix(a: str, b: str, t: float) -> str:
    ca, cb = Gdk.RGBA(), Gdk.RGBA()
    ca.parse(a)
    cb.parse(b)
    r, g, bl = (ca.red * (1 - t) + cb.red * t, ca.green * (1 - t) + cb.green * t, ca.blue * (1 - t) + cb.blue * t)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(bl * 255):02x}"


BASE_CSS = """
.wf-brand-icon { background: @accent_bg_color; color: @accent_fg_color; border-radius: 12px;
                 min-width: 34px; min-height: 34px; }
.wf-brand-title { font-weight: 800; font-size: 15px; letter-spacing: 0.3px; }
.wf-brand-sub { font-size: 11px; opacity: 0.55; }
.wf-hero { border-radius: 18px; padding: 24px 26px;
           background: linear-gradient(135deg, alpha(@accent_bg_color, 0.22), alpha(@accent_bg_color, 0.04) 60%),
                       @card_bg_color;
           border: 1px solid alpha(@accent_bg_color, 0.25); }
.wf-hero-title { font-size: 26px; font-weight: 800; }
.wf-hero-sub { font-size: 14px; opacity: 0.75; }
.wf-keycap { background: alpha(@window_fg_color, 0.08); border: 1px solid alpha(@window_fg_color, 0.15);
             border-bottom-width: 2px; border-radius: 7px; padding: 3px 9px; font-size: 12px; font-weight: 700;
             font-family: monospace; }
.wf-plus { opacity: 0.5; font-size: 12px; }
.wf-hint { font-size: 12px; opacity: 0.7; }
.wf-stat { border-radius: 16px; padding: 16px 18px; }
.wf-stat-icon { background: alpha(@accent_bg_color, 0.16); color: @accent_color; border-radius: 10px;
                min-width: 34px; min-height: 34px; }
.wf-stat-value { font-size: 26px; font-weight: 800; }
.wf-stat-label { font-size: 12px; opacity: 0.6; }
.wf-section { font-size: 13px; font-weight: 700; opacity: 0.85; }
.wf-card { border-radius: 16px; padding: 16px 18px; }
.wf-recent-text { font-size: 13px; }
.wf-recent-meta { font-size: 11px; opacity: 0.5; }
.wf-app-bar { min-height: 6px; border-radius: 3px; }
.wf-app-bar trough { min-height: 6px; border-radius: 3px; background: alpha(@window_fg_color, 0.08); }
.wf-app-bar progress { min-height: 6px; border-radius: 3px; background: @accent_bg_color; }
.wf-status-dot { min-width: 8px; min-height: 8px; border-radius: 4px; background: alpha(@window_fg_color, 0.3); }
.wf-status-dot.ok { background: #4caf50; }
.wf-status-dot.rec { background: @accent_bg_color; }
.wf-day { font-size: 11px; opacity: 0.55; }
.wf-empty { opacity: 0.5; font-size: 13px; }
.navigation-sidebar row { border-radius: 10px; margin: 1px 6px; }
.navigation-sidebar row:selected { background: alpha(@accent_bg_color, 0.18); color: @accent_color; }
"""


def apply(display: Gdk.Display | None = None) -> None:
    display = display or Gdk.Display.get_default()
    c = colors()
    css = ""
    if c.get("mode", "dark") == "dark":
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    else:
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
    if c.get("accent") and c.get("background"):
        acc, bg, fg = c["accent"], c["background"], c.get("foreground", "#dddddd")
        css += f"""
@define-color accent_bg_color {acc};
@define-color accent_color {_mix(acc, fg, 0.15)};
@define-color accent_fg_color {c.get('darker_background', bg)};
@define-color window_bg_color {bg};
@define-color window_fg_color {fg};
@define-color view_bg_color {bg};
@define-color view_fg_color {fg};
@define-color headerbar_bg_color {bg};
@define-color headerbar_fg_color {fg};
@define-color sidebar_bg_color {c.get('dark_background', bg)};
@define-color sidebar_fg_color {fg};
@define-color secondary_sidebar_bg_color {c.get('dark_background', bg)};
@define-color card_bg_color {c.get('lighter_background', _mix(bg, fg, 0.06))};
@define-color card_fg_color {fg};
@define-color popover_bg_color {c.get('lighter_background', bg)};
@define-color popover_fg_color {fg};
@define-color dialog_bg_color {c.get('lighter_background', bg)};
@define-color dialog_fg_color {fg};
@define-color destructive_bg_color {c.get('red', '#c01c28')};
"""
    prov = Gtk.CssProvider()
    prov.load_from_string(css + BASE_CSS)
    Gtk.StyleContext.add_provider_for_display(display, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
