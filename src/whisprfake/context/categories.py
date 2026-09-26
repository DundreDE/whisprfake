"""App -> style category mapping (Wispr: personal / work / email / other).

Matching runs on the Hyprland window class, window title and (when AT-SPI provides it) the browser URL,
so web apps like web.whatsapp.com or mail.google.com are recognised as well."""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULTS: dict[str, str] = {
    # personal messaging
    "signal": "personal", "whatsapp": "personal", "telegram": "personal", "org.telegram": "personal",
    "discord": "personal", "messenger": "personal", "instagram.com/direct": "personal", "threema": "personal",
    "element": "personal", "beeper": "personal", "web.whatsapp.com": "personal",
    # work messaging
    "slack": "work", "teams": "work", "teams.microsoft.com": "work", "linkedin": "work", "mattermost": "work",
    "zulip": "work", "rocket.chat": "work", "app.slack.com": "work", "linear.app": "work", "notion": "work",
    # email
    "thunderbird": "email", "betterbird": "email", "evolution": "email", "geary": "email", "mail.google.com": "email",
    "gmail": "email", "outlook": "email", "outlook.office.com": "email", "outlook.live.com": "email",
    "proton.me/mail": "email", "mail.proton.me": "email", "hey.com": "email", "fastmail": "email",
    "tuta": "email", "posteo": "email", "gmx": "email", "web.de": "email",
}

TERMINALS = {"alacritty", "kitty", "com.mitchellh.ghostty", "ghostty", "foot", "footclient", "org.wezfurlong.wezterm",
             "wezterm", "xterm", "konsole", "gnome-terminal", "org.gnome.console", "tilix", "st"}
CODE_EDITORS = {"code", "code-oss", "vscodium", "cursor", "windsurf", "zed", "dev.zed.zed", "jetbrains", "neovide"}


@dataclass
class AppInfo:
    wm_class: str = ""
    title: str = ""
    url: str = ""
    pid: int = 0

    @property
    def is_terminal(self) -> bool:
        return self.wm_class.lower() in TERMINALS

    @property
    def is_code(self) -> bool:
        c = self.wm_class.lower()
        return any(c.startswith(x) for x in CODE_EDITORS) or self.is_terminal


def categorize(app: AppInfo, overrides: dict[str, str] | None = None) -> str:
    table = {**DEFAULTS, **{k.lower(): v for k, v in (overrides or {}).items()}}
    # Most specific source first: URL, then class, then title. Longest key wins within a source.
    for hay in (app.url.lower(), app.wm_class.lower(), app.title.lower()):
        if not hay:
            continue
        hits = [k for k in table if re.search(rf"(?<![\w]){re.escape(k)}(?![\w])", hay)]
        if hits:
            return table[max(hits, key=len)]
    return "other"
