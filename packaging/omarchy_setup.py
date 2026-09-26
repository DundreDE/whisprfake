"""Idempotently register whisprfake with omarchy-shell: bar widget in the layout, menu entries."""

import json
import os
import re
from pathlib import Path

CFG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "omarchy"
PLUGIN = "jakob.whisprfake"


def bar_layout() -> None:
    p = CFG / "shell.json"
    if not p.exists():
        print("   shell.json fehlt – Widget später mit `omarchy bar` hinzufügen")
        return
    d = json.loads(p.read_text())
    # A plugin that is both service and bar widget belongs in the bar layout (like omarchy.media).
    d["plugins"] = [x for x in d.get("plugins", []) if x.get("id") != PLUGIN]
    layout = d.setdefault("bar", {}).setdefault("layout", {})
    if not any(w.get("id") == PLUGIN for sec in layout.values() for w in sec):
        right = layout.setdefault("right", [])
        i = next((k for k, w in enumerate(right) if w.get("id") == "omarchy.audio"), len(right))
        right.insert(i, {"id": PLUGIN})
    d["disabledPlugins"] = [x for x in d.get("disabledPlugins", []) if x != PLUGIN]
    p.write_text(json.dumps(d, indent=2) + "\n")
    print("   Leisten-Symbol eingetragen")


MENU = {
    "whisprfake": {"icon": "󰍬", "label": "Diktat", "description": "whisprfake – lokale Spracheingabe"},
    "whisprfake.hub": {"icon": "󰕮", "label": "Hub öffnen", "action": "whisprfake hub"},
    "whisprfake.settings": {"icon": "", "label": "Einstellungen", "action": "whisprfake hub --page Einstellungen"},
    "whisprfake.scratchpad": {"icon": "󰎞", "label": "Scratchpad", "action": "whisprfake scratchpad"},
    "whisprfake.handsfree": {"icon": "󰍬", "label": "Freihändig diktieren", "action": "whisprfake ctl toggle"},
    "whisprfake.paste-last": {"icon": "󰆒", "label": "Letztes Diktat einfügen",
                              "action": "sleep 0.3; whisprfake ctl paste_last"},
    "whisprfake.meeting-start": {"icon": "󰑊", "label": "Meeting aufnehmen",
                                 "when": "! whisprfake ctl meeting.status | grep -q '\"recording\": true'",
                                 "action": "whisprfake ctl meeting.start"},
    "whisprfake.meeting-stop": {"icon": "󰓛", "label": "Meeting-Aufnahme beenden",
                                "when": "whisprfake ctl meeting.status | grep -q '\"recording\": true'",
                                "action": "whisprfake ctl meeting.stop"},
    "whisprfake.transform": {"icon": "󰑐", "label": "Markierten Text umwandeln"},
}
for key, name in [("shorter", "Kürzer"), ("email", "Als E-Mail"), ("en", "Auf Englisch"), ("de", "Auf Deutsch"),
                  ("bullets", "Stichpunkte"), ("spelling", "Rechtschreibung")]:
    MENU[f"whisprfake.transform.{key}"] = {
        "label": name, "action": f"sleep 0.3; whisprfake ctl transforms.run '{json.dumps({'name': name}, ensure_ascii=False)}'"}


def menu() -> None:
    p = CFG / "extensions" / "omarchy-menu.jsonc"
    p.parent.mkdir(parents=True, exist_ok=True)
    s = p.read_text() if p.exists() else "{\n}\n"
    # drop a previous whisprfake block, then append the current one before the final brace
    s = re.sub(r"\n  // whisprfake \(local Wispr Flow\).*?(?=\n\})", "", s, flags=re.S)
    lines = ["", "  // whisprfake (local Wispr Flow) – managed by packaging/install.sh"]
    lines += [f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}," for k, v in MENU.items()]
    i = s.rstrip().rfind("}")
    s = s[:i].rstrip() + "\n" + "\n".join(lines) + "\n}\n"
    p.write_text(s)
    print("   Omarchy-Menü: Diktat")


if __name__ == "__main__":
    bar_layout()
    menu()
