"""Einstellungen: shortcuts, microphone, recognition, AI cleanup, privacy, service."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from ..client import call_async  # noqa: E402
from .pages import Page  # noqa: E402

KEYNAMES = {
    "Control_L": "CTRL", "Control_R": "CTRL", "Super_L": "SUPER", "Super_R": "SUPER", "Alt_L": "ALT", "Alt_R": "ALT",
    "ISO_Level3_Shift": "ALT", "Shift_L": "SHIFT", "Shift_R": "SHIFT", "Meta_L": "SUPER", "Meta_R": "SUPER",
    "space": "SPACE", "Escape": "ESC", "Return": "ENTER", "Caps_Lock": "CAPSLOCK",
}
ORDER = ["CTRL", "SUPER", "ALT", "SHIFT"]


def pretty(chord: str) -> str:
    names = {"CTRL": "Ctrl", "SUPER": "Super", "ALT": "Alt", "SHIFT": "Shift", "SPACE": "Leertaste", "ESC": "Esc"}
    return " + ".join(names.get(p, p.title()) for p in chord.split("+")) if chord else "—"


class ShortcutDialog(Adw.Dialog):
    """Press and release a chord; modifier-only chords (like Ctrl+Super) are allowed."""

    def __init__(self, on_done):
        super().__init__(title="Tastenkürzel aufnehmen", content_width=420, content_height=220)
        self.on_done, self.down, self.best = on_done, set(), set()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=24, margin_bottom=24,
                      margin_start=24, margin_end=24)
        box.append(Gtk.Label(label="Drücke die gewünschte Kombination und lass los.", wrap=True))
        self.label = Gtk.Label(label="…")
        self.label.add_css_class("title-2")
        box.append(self.label)
        tb = Adw.ToolbarView(content=box)
        tb.add_top_bar(Adw.HeaderBar())
        self.set_child(tb)
        ctl = Gtk.EventControllerKey()
        ctl.connect("key-pressed", self._press)
        ctl.connect("key-released", self._release)
        self.add_controller(ctl)

    def _name(self, keyval) -> str:
        n = Gdk.keyval_name(keyval) or ""
        return KEYNAMES.get(n, n.upper())

    def _press(self, _c, keyval, _code, _state):
        self.down.add(self._name(keyval))
        if len(self.down) >= len(self.best):
            self.best = set(self.down)
        self.label.set_label(pretty(self._chord()))
        return True

    def _release(self, _c, keyval, _code, _state):
        self.down.discard(self._name(keyval))
        if not self.down and self.best:
            self.on_done(self._chord())
            self.close()

    def _chord(self) -> str:
        mods = [m for m in ORDER if m in self.best]
        rest = sorted(k for k in self.best if k not in ORDER)
        return "+".join(mods + rest)


class SettingsPage(Page):
    title, icon = "Einstellungen", "emblem-system-symbolic"

    def build(self):
        self.page = Adw.PreferencesPage()
        self._loading = False
        # --- shortcuts
        g = Adw.PreferencesGroup(title="Tastenkürzel",
                                 description="Halten = Push-to-talk. Doppeltipp auf Push-to-talk = freihändig.")
        self.sc_rows = {}
        for key, title in [("push_to_talk", "Push-to-talk (halten)"), ("hands_free", "Freihändig an/aus"),
                           ("command", "Command Mode (halten)"), ("paste_last", "Letztes Diktat erneut einfügen")]:
            row = Adw.ActionRow(title=title)
            lab = Gtk.Label(valign=Gtk.Align.CENTER)
            lab.add_css_class("dim-label")
            row.add_suffix(lab)
            b = Gtk.Button(label="Ändern", valign=Gtk.Align.CENTER)
            b.connect("clicked", self._record, key)
            row.add_suffix(b)
            clr = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Entfernen")
            clr.add_css_class("flat")
            clr.connect("clicked", lambda *_a, k=key: self._set_shortcut(k, None))
            row.add_suffix(clr)
            row._lab = lab
            self.sc_rows[key] = row
            g.add(row)
        self.page.add(g)
        # --- audio
        g = Adw.PreferencesGroup(title="Mikrofon &amp; Töne")
        self.mic = Adw.ComboRow(title="Mikrofon", subtitle="Bevorzugtes Eingabegerät")
        self.mic.connect("notify::selected", self._mic_changed)
        g.add(self.mic)
        self.sounds = Adw.SwitchRow(title="Start-/Stopp-Töne")
        self.sounds.connect("notify::active", self._audio_changed)
        g.add(self.sounds)
        self.volume = Adw.SpinRow.new_with_range(0, 100, 5)
        self.volume.set_title("Lautstärke der Töne (%)")
        self.volume.connect("notify::value", self._audio_changed)
        g.add(self.volume)
        self.pause = Adw.SwitchRow(title="Musik beim Diktieren pausieren")
        self.pause.connect("notify::active", self._audio_changed)
        g.add(self.pause)
        self.maxmin = Adw.SpinRow.new_with_range(1, 60, 1)
        self.maxmin.set_title("Maximale Diktatdauer (Minuten)")
        self.maxmin.connect("notify::value", self._audio_changed)
        g.add(self.maxmin)
        self.page.add(g)
        # --- recognition
        g = Adw.PreferencesGroup(title="Spracherkennung", description="Alles läuft lokal auf deiner GPU.")
        self.engines = [("qwen3asr", "Qwen3-ASR 1.7B – empfohlen, hört dein Wörterbuch (GPU)"),
                        ("parakeet", "Parakeet v3 – sehr schnell, ohne Wörterbuch (GPU)"),
                        ("whisper", "Whisper large-v3-turbo – langsamer (GPU)"),
                        ("parakeet_onnx", "Parakeet v3 (CPU, ohne Grafikkarte)")]
        self.engine = Adw.ComboRow(title="Engine", model=Gtk.StringList.new([e[1] for e in self.engines]))
        self.engine.connect("notify::selected", self._asr_changed)
        g.add(self.engine)
        self.page.add(g)
        # --- cleanup
        g = Adw.PreferencesGroup(title="KI-Bereinigung")
        self.levels = [("none", "Aus – Rohtext"), ("light", "Leicht – nur Füllwörter und Satzzeichen"),
                       ("medium", "Mittel – empfohlen"), ("high", "Stark – glättet Formulierungen")]
        self.level = Adw.ComboRow(title="Stufe", model=Gtk.StringList.new([x[1] for x in self.levels]))
        self.level.connect("notify::selected", self._cleanup_changed)
        g.add(self.level)
        self.cleanup_model = Adw.ComboRow(title="Modell für Diktate", subtitle="klein = schnell")
        self.cleanup_model.connect("notify::selected", self._llm_changed)
        g.add(self.cleanup_model)
        self.command_model = Adw.ComboRow(title="Modell für Befehle &amp; Meetings")
        self.command_model.connect("notify::selected", self._llm_changed)
        g.add(self.command_model)
        self.page.add(g)
        # --- privacy
        g = Adw.PreferencesGroup(title="Privatsphäre", description="Nichts verlässt deinen Rechner.")
        self.context = Adw.SwitchRow(title="Kontext-Erkennung",
                                     subtitle="Liest App, Text um den Cursor und Namen auf dem Bildschirm (nie Passwortfelder)")
        self.context.connect("notify::active", self._privacy_changed)
        g.add(self.context)
        self.autolearn = Adw.SwitchRow(title="Wörterbuch-Vorschläge",
                                       subtitle="Erkennt, wenn du ein diktiertes Wort korrigierst")
        self.autolearn.connect("notify::active", self._privacy_changed)
        g.add(self.autolearn)
        self.retention = Adw.SpinRow.new_with_range(0, 365, 1)
        self.retention.set_title("Audio aufbewahren (Tage)")
        self.retention.set_subtitle("0 = Audio sofort nach Verarbeitung löschen")
        self.retention.connect("notify::value", self._privacy_changed)
        g.add(self.retention)
        self.page.add(g)
        # --- service
        g = Adw.PreferencesGroup(title="Dienst")
        r = Adw.ButtonRow(title="Dienst neu starten", start_icon_name="view-refresh-symbolic")
        r.connect("activated", lambda *_: call_async("service.restart", {}, lambda *_: self.toast("Dienst startet neu …")))
        g.add(r)
        self.page.add(g)
        return self.page

    # ------------------------------------------------------------------ load
    def refresh(self):
        self.cfg = self.safe("config.get")
        if not self.cfg:
            return
        self._loading = True
        c = self.cfg
        for key, row in self.sc_rows.items():
            row._lab.set_label(pretty((c["shortcuts"][key] or [""])[0]))
        self.devices = ["System-Standard"] + (self.safe("devices") or [])
        self.mic.set_model(Gtk.StringList.new(self.devices))
        pri = c["audio"]["mic_priority"]
        self.mic.set_selected(self.devices.index(pri[0]) if pri and pri[0] in self.devices else 0)
        self.sounds.set_active(c["audio"]["sounds"])
        self.volume.set_value(round(c["audio"]["sound_volume"] * 100))
        self.pause.set_active(c["audio"]["pause_media"])
        self.maxmin.set_value(c["audio"]["max_minutes"])
        self.engine.set_selected(next((i for i, e in enumerate(self.engines) if e[0] == c["asr"]["engine"]), 0))
        self.level.set_selected(next((i for i, e in enumerate(self.levels) if e[0] == c["cleanup"]["level"]), 2))
        self.models = self.safe("models.list") or [c["llm"]["cleanup_model"]]
        for row, cur in ((self.cleanup_model, c["llm"]["cleanup_model"]), (self.command_model, c["llm"]["command_model"])):
            row.set_model(Gtk.StringList.new(self.models))
            row.set_selected(self.models.index(cur) if cur in self.models else 0)
        self.context.set_active(c["privacy"]["context_awareness"])
        self.autolearn.set_active(c["privacy"]["autolearn_suggestions"])
        self.retention.set_value(c["privacy"]["audio_retention_days"])
        self._loading = False

    def _save(self, section: str, values: dict, msg: str = "Gespeichert") -> None:
        if self._loading or not getattr(self, "cfg", None):
            return
        self.cfg[section] = {**self.cfg[section], **values}
        self.safe("config.set", {section: self.cfg[section]}, ok=msg)

    # ------------------------------------------------------------------ handlers
    def _record(self, _b, key):
        ShortcutDialog(lambda chord: self._set_shortcut(key, chord)).present(self.win)

    def _set_shortcut(self, key, chord):
        self._save("shortcuts", {key: [chord] if chord else []}, "Tastenkürzel gespeichert")
        self.sc_rows[key]._lab.set_label(pretty(chord or ""))

    def _mic_changed(self, *_):
        i = self.mic.get_selected()
        self._save("audio", {"mic_priority": [] if i <= 0 else [self.devices[i]]}, "Mikrofon gespeichert")

    def _audio_changed(self, *_):
        self._save("audio", {"sounds": self.sounds.get_active(), "sound_volume": self.volume.get_value() / 100,
                             "pause_media": self.pause.get_active(), "max_minutes": int(self.maxmin.get_value()),
                             "warn_minutes": max(0, int(self.maxmin.get_value()) - 1)})

    def _asr_changed(self, *_):
        self._save("asr", {"engine": self.engines[self.engine.get_selected()][0]},
                   "Engine gewechselt – Modell wird geladen …")

    def _cleanup_changed(self, *_):
        self._save("cleanup", {"level": self.levels[self.level.get_selected()][0]})

    def _llm_changed(self, *_):
        if not getattr(self, "models", None):
            return
        self._save("llm", {"cleanup_model": self.models[self.cleanup_model.get_selected()],
                           "command_model": self.models[self.command_model.get_selected()],
                           "summary_model": self.models[self.command_model.get_selected()]})

    def _privacy_changed(self, *_):
        self._save("privacy", {"context_awareness": self.context.get_active(),
                               "autolearn_suggestions": self.autolearn.get_active(),
                               "audio_retention_days": int(self.retention.get_value())})
