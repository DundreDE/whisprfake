"""Hub pages: Übersicht, Verlauf, Wörterbuch, Snippets, Styles, Transforms, Notizen, Meetings."""

from __future__ import annotations

import json
import subprocess

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..client import DaemonError, call, call_async  # noqa: E402
from .widgets import add, clear, esc, icon_button, stat_card, when  # noqa: E402


class Page:
    title = ""
    icon = ""

    def __init__(self, win):
        self.win = win
        self.widget = self.build()

    def build(self) -> Gtk.Widget:
        raise NotImplementedError

    def refresh(self) -> None:
        pass

    def toast(self, msg: str) -> None:
        self.win.toast(msg)

    def safe(self, method: str, params: dict | None = None, ok: str | None = None):
        try:
            r = call(method, params)
            if ok:
                self.toast(ok)
            return r
        except DaemonError as e:
            self.toast(str(e))
            return None


# --------------------------------------------------------------------------- Übersicht
class HomePage(Page):
    title, icon = "Übersicht", "go-home-symbolic"

    def build(self):
        self.page = Adw.PreferencesPage()
        head = Adw.PreferencesGroup(title="Deine Stimme",
                                    description="Halte Ctrl+Super und sprich – Doppeltipp für freihändig, "
                                                "Ctrl+Super+Alt für Befehle, Esc bricht ab.")
        grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
        self.cards = {}
        for i, (key, label) in enumerate([("today_words", "Wörter heute"), ("week_words", "Wörter diese Woche"),
                                          ("total_words", "Wörter gesamt"), ("wpm", "Wörter pro Minute"),
                                          ("streak_days", "Tage in Folge"), ("minutes_saved", "Minuten gespart")]):
            card, lab = stat_card("–", label)
            self.cards[key] = lab
            grid.attach(card, i % 3, i // 3, 1, 1)
        head.add(grid)
        self.page.add(head)
        self.status = Adw.PreferencesGroup(title="Status")
        self.page.add(self.status)
        self.apps = Adw.PreferencesGroup(title="Meistgenutzte Apps")
        self.page.add(self.apps)
        self.recent = Adw.PreferencesGroup(title="Zuletzt diktiert")
        self.page.add(self.recent)
        return self.page

    def refresh(self):
        stats = self.safe("stats") or {}
        for k, lab in self.cards.items():
            v = stats.get(k, 0)
            lab.set_label(f"{v:,}".replace(",", ".") if isinstance(v, int) else str(v))
        clear(self.status)
        st = self.safe("status")
        if st:
            add(self.status, Adw.ActionRow(title="Dienst", subtitle="läuft" + (" – bereit" if st["asr_ready"] else " – lädt Modelle …")))
            add(self.status, Adw.ActionRow(title="Spracherkennung", subtitle=st["asr"]))
            add(self.status, Adw.ActionRow(title="KI-Bereinigung", subtitle=st["llm"]))
        else:
            add(self.status, Adw.ActionRow(title="Dienst", subtitle="nicht erreichbar – `systemctl --user start whisprfake`"))
        clear(self.apps)
        for app, words in stats.get("top_apps", []):
            add(self.apps, Adw.ActionRow(title=esc(app), subtitle=f"{words} Wörter"))
        clear(self.recent)
        for r in (self.safe("history", {"limit": 5}) or []):
            if r.get("cleaned"):
                add(self.recent, Adw.ActionRow(title=esc(r["cleaned"][:140]), subtitle=when(r["ts"]),
                                               title_lines=2))


# --------------------------------------------------------------------------- Verlauf
class HistoryPage(Page):
    title, icon = "Verlauf", "document-open-recent-symbolic"
    STATUS = {"inserted": "eingefügt", "empty": "leer", "failed": "fehlgeschlagen", "pending": "in Arbeit",
              "recovered": "wiederhergestellt", "answered": "beantwortet", "cancelled": "abgebrochen"}

    def build(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bar = Gtk.Box(spacing=8, margin_top=12, margin_start=24, margin_end=24)
        self.search = Gtk.SearchEntry(placeholder_text="Verlauf durchsuchen …", hexpand=True)
        self.search.connect("search-changed", lambda *_: self.refresh())
        bar.append(self.search)
        clear_btn = Gtk.Button(label="Alles löschen")
        clear_btn.add_css_class("destructive-action")
        clear_btn.connect("clicked", self._confirm_clear)
        bar.append(clear_btn)
        box.append(bar)
        self.page = Adw.PreferencesPage(vexpand=True)
        self.group = Adw.PreferencesGroup()
        self.page.add(self.group)
        box.append(self.page)
        return box

    def refresh(self):
        clear(self.group)
        rows = self.safe("history", {"limit": 300, "search": self.search.get_text()}) or []
        if not rows:
            add(self.group, Adw.ActionRow(title="Noch nichts diktiert"))
        for r in rows:
            text = r.get("cleaned") or r.get("raw") or ""
            meta = [when(r["ts"]), r.get("app_class") or "", self.STATUS.get(r["status"], r["status"])]
            if r.get("mode") in ("command", "answer"):
                meta.insert(1, "Befehl")
            if r.get("latency_ms"):
                meta.append(f"{r['latency_ms']} ms")
            exp = Adw.ExpanderRow(title=esc(text[:160]) or "<i>(leer)</i>", subtitle=" · ".join(m for m in meta if m))
            exp.set_title_lines(2)
            if r.get("instruction"):
                exp.add_row(Adw.ActionRow(title="Befehl", subtitle=esc(r["instruction"]), subtitle_selectable=True))
            exp.add_row(Adw.ActionRow(title="Erkannt (roh)", subtitle=esc(r.get("raw") or ""), subtitle_selectable=True))
            exp.add_row(Adw.ActionRow(title="Bereinigt", subtitle=esc(text), subtitle_selectable=True))
            info = f"Erkennung {r.get('asr_engine') or '–'} {r.get('asr_ms') or 0} ms · LLM {r.get('llm_ms') or 0} ms"
            if r.get("guard") and r["guard"] != "ok":
                info += f" · Fallback: {r['guard']}"
            actions = Adw.ActionRow(title=info)
            actions.add_suffix(icon_button("document-edit-symbolic", "Korrigieren (whisprfake lernt daraus)",
                                           self._correct, r["id"], text))
            actions.add_suffix(icon_button("edit-copy-symbolic", "Kopieren", self._copy, text))
            if r.get("audio_path"):
                actions.add_suffix(icon_button("media-playback-start-symbolic", "Audio abspielen",
                                               lambda i: self.safe("audio.play", {"id": i}), r["id"]))
                actions.add_suffix(icon_button("view-refresh-symbolic", "Neu verarbeiten", self._retry, r["id"]))
            actions.add_suffix(icon_button("user-trash-symbolic", "Löschen", self._delete, r["id"]))
            exp.add_row(actions)
            add(self.group, exp)

    def _copy(self, text):
        self.win.get_clipboard().set(text)
        self.toast("Kopiert")

    def _correct(self, did, text):
        d = Adw.AlertDialog(heading="Diktat korrigieren",
                            body="Verbessere falsch erkannte Namen oder Begriffe – sie landen automatisch im Wörterbuch.")
        view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8, left_margin=8,
                            right_margin=8)
        view.get_buffer().set_text(text)
        d.set_extra_child(Gtk.Frame(child=Gtk.ScrolledWindow(child=view, min_content_height=120, min_content_width=420)))
        d.add_response("cancel", "Abbrechen")
        d.add_response("save", "Speichern")
        d.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        def done(_d, resp):
            if resp != "save":
                return
            buf = view.get_buffer()
            learned = self.safe("history.correct", {"id": did, "text": buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)})
            self.toast("Gelernt: " + ", ".join(learned) if learned else "Gespeichert")
            self.refresh()

        d.connect("response", done)
        d.present(self.win)

    def _retry(self, did):
        self.toast("Verarbeite neu …")
        call_async("retry", {"id": did}, lambda r, e: (self.toast(str(e) if e else "Neu verarbeitet"), self.refresh()))

    def _delete(self, did):
        self.safe("history.delete", {"id": did})
        self.refresh()

    def _confirm_clear(self, *_):
        d = Adw.AlertDialog(heading="Gesamten Verlauf löschen?", body="Texte und Audioaufnahmen werden entfernt.")
        d.add_response("cancel", "Abbrechen")
        d.add_response("delete", "Löschen")
        d.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        d.connect("response", lambda _d, r: r == "delete" and (self.safe("history.clear"), self.refresh()))
        d.present(self.win)


# --------------------------------------------------------------------------- Wörterbuch
class DictionaryPage(Page):
    title, icon = "Wörterbuch", "accessories-dictionary-symbolic"

    def build(self):
        self.page = Adw.PreferencesPage()
        addg = Adw.PreferencesGroup(title="Neues Wort",
                                    description="Namen, Fachbegriffe, Firmen, Orte. Die Spracherkennung bekommt diese Liste als "
                                                "Kontext und hört sie dadurch richtig. „Klingt wie“ korrigiert zusätzlich, "
                                                "was trotzdem falsch ankommt.")
        self.term = Adw.EntryRow(title="Wort / Begriff (mehrere mit Komma trennen)")
        self.sounds = Adw.EntryRow(title="Klingt wie (optional, mit Komma trennen)")
        addg.add(self.term)
        addg.add(self.sounds)
        btn = Adw.ButtonRow(title="Hinzufügen", start_icon_name="list-add-symbolic")
        btn.connect("activated", self._add)
        self.term.connect("entry-activated", self._add)
        addg.add(btn)
        self.page.add(addg)
        self.sugg = Adw.PreferencesGroup(title="Vorschläge", description="Wörter, die du nach dem Diktieren korrigiert hast")
        self.page.add(self.sugg)
        self.search = Gtk.SearchEntry(placeholder_text="Suchen …", valign=Gtk.Align.CENTER)
        self.search.connect("search-changed", lambda *_: self.refresh())
        self.list = Adw.PreferencesGroup(title="Dein Wörterbuch", header_suffix=self.search)
        self.page.add(self.list)
        return self.page

    def _add(self, *_):
        term = self.term.get_text().strip()
        if not term:
            return
        sounds = [s.strip() for s in self.sounds.get_text().split(",") if s.strip()]
        terms = [t.strip() for t in term.split(",") if t.strip()] if not sounds else [term]
        for t in terms:  # "A, B, C" adds several terms at once
            self.safe("dictionary.add", {"term": t, "sounds_like": sounds})
        self.toast(f"„{terms[0]}“ hinzugefügt" if len(terms) == 1 else f"{len(terms)} Begriffe hinzugefügt")
        self.term.set_text("")
        self.sounds.set_text("")
        self.refresh()

    def refresh(self):
        clear(self.sugg)
        sugg = self.safe("suggestions.list") or []
        self.sugg.set_visible(bool(sugg))
        for s in sugg:
            row = Adw.ActionRow(title=esc(s["term"]), subtitle=f"statt „{esc(s['heard'])}“" if s.get("heard") else "")
            row.add_suffix(icon_button("object-select-symbolic", "Übernehmen", self._resolve, s["id"], True))
            row.add_suffix(icon_button("window-close-symbolic", "Verwerfen", self._resolve, s["id"], False))
            add(self.sugg, row)
        clear(self.list)
        q = self.search.get_text().lower()
        for t in self.safe("dictionary.list") or []:
            sounds = json.loads(t.get("sounds_like") or "[]")
            if q and q not in t["term"].lower() and not any(q in x.lower() for x in sounds):
                continue
            row = Adw.ActionRow(title=esc(t["term"]), subtitle=esc(", ".join(sounds)))
            star = Gtk.ToggleButton(icon_name="starred-symbolic" if t["starred"] else "non-starred-symbolic",
                                    active=bool(t["starred"]), valign=Gtk.Align.CENTER, tooltip_text="Wichtig")
            star.add_css_class("flat")
            star.connect("toggled", lambda b, i=t["id"]: (self.safe("dictionary.star", {"id": i, "starred": b.get_active()}),
                                                         b.set_icon_name("starred-symbolic" if b.get_active() else "non-starred-symbolic")))
            row.add_suffix(star)
            row.add_suffix(icon_button("user-trash-symbolic", "Entfernen", self._remove, t["id"]))
            add(self.list, row)

    def _resolve(self, sid, accept):
        self.safe("suggestions.resolve", {"id": sid, "accept": accept})
        self.refresh()

    def _remove(self, tid):
        self.safe("dictionary.remove", {"id": tid})
        self.refresh()


# --------------------------------------------------------------------------- Snippets
class SnippetsPage(Page):
    title, icon = "Snippets", "insert-text-symbolic"

    def build(self):
        self.page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Neues Snippet",
                                 description="Sag den Auslöser beim Diktieren – er wird durch den Text ersetzt "
                                             "(z. B. „mein Kalenderlink“ → https://cal.com/…).")
        self.trigger = Adw.EntryRow(title="Auslöser (max. 60 Zeichen)")
        g.add(self.trigger)
        self.text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8, left_margin=10,
                                 right_margin=10)
        frame = Gtk.Frame(child=Gtk.ScrolledWindow(child=self.text, min_content_height=90), margin_top=8)
        g.add(frame)
        btn = Adw.ButtonRow(title="Speichern", start_icon_name="document-save-symbolic")
        btn.connect("activated", self._save)
        g.add(btn)
        self.page.add(g)
        self.list = Adw.PreferencesGroup(title="Deine Snippets")
        self.page.add(self.list)
        return self.page

    def _save(self, *_):
        trig = self.trigger.get_text().strip()
        buf = self.text.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        if not trig or not text.strip():
            self.toast("Auslöser und Text ausfüllen")
            return
        self.safe("snippets.add", {"trigger": trig, "text": text}, ok="Snippet gespeichert")
        self.trigger.set_text("")
        buf.set_text("")
        self.refresh()

    def refresh(self):
        clear(self.list)
        for sn in self.safe("snippets.list") or []:
            row = Adw.ActionRow(title=esc(sn["trigger"]), subtitle=esc(sn["text"][:200]), subtitle_lines=3)
            row.add_suffix(icon_button("document-edit-symbolic", "Bearbeiten", self._edit, sn))
            row.add_suffix(icon_button("user-trash-symbolic", "Löschen", self._remove, sn["id"]))
            add(self.list, row)

    def _edit(self, sn):
        self.trigger.set_text(sn["trigger"])
        self.text.get_buffer().set_text(sn["text"])

    def _remove(self, sid):
        self.safe("snippets.remove", {"id": sid})
        self.refresh()


# --------------------------------------------------------------------------- Styles
class StylesPage(Page):
    title, icon = "Styles", "applications-graphics-symbolic"
    CATS = [("personal", "Private Nachrichten", "Signal, WhatsApp, Telegram, Discord …",
             [("formal", "Formell."), ("casual", "Locker"), ("very_casual", "sehr locker")]),
            ("work", "Arbeits-Chat", "Slack, Teams, LinkedIn …",
             [("formal", "Formell."), ("casual", "Locker"), ("excited", "Begeistert!")]),
            ("email", "E-Mail", "Thunderbird, Gmail, Outlook …",
             [("formal", "Formell."), ("casual", "Locker"), ("excited", "Begeistert!")]),
            ("other", "Alles andere", "Editoren, Terminal, KI-Chats, Dokumente …",
             [("formal", "Formell."), ("casual", "Locker"), ("excited", "Begeistert!")])]
    DESC = {"formal": "Groß-/Kleinschreibung und vollständige Satzzeichen",
            "casual": "Großschreibung, weniger Satzzeichen, kein Schlusspunkt bei kurzen Nachrichten",
            "very_casual": "alles klein, kaum Satzzeichen",
            "excited": "mehr Ausrufezeichen, wo es passt"}

    def build(self):
        self.page = Adw.PreferencesPage()
        self.group = Adw.PreferencesGroup(title="Schreibstil je App-Kategorie",
                                          description="whisprfake erkennt die App (auch Web-Apps im Browser) und "
                                                      "formatiert passend – auf Deutsch und Englisch.")
        self.combos = {}
        for key, title, sub, opts in self.CATS:
            model = Gtk.StringList.new([label for _, label in opts])
            row = Adw.ComboRow(title=title, subtitle=sub, model=model)
            row._opts = opts
            row.connect("notify::selected", self._changed, key)
            self.combos[key] = row
            self.group.add(row)
        self.page.add(self.group)
        og = Adw.PreferencesGroup(title="Eigene App-Zuordnung",
                                  description="Teil des Fensterklassen-Namens, Fenstertitels oder der URL → Kategorie")
        self.pattern = Adw.EntryRow(title="z. B. „element“ oder „mail.firma.de“")
        og.add(self.pattern)
        self.cat = Adw.ComboRow(title="Kategorie", model=Gtk.StringList.new([c[1] for c in self.CATS]))
        og.add(self.cat)
        btn = Adw.ButtonRow(title="Zuordnung hinzufügen", start_icon_name="list-add-symbolic")
        btn.connect("activated", self._add_override)
        og.add(btn)
        self.page.add(og)
        self.overrides = Adw.PreferencesGroup()
        self.page.add(self.overrides)
        self._loading = False
        return self.page

    def refresh(self):
        self.cfg = self.safe("config.get")
        if not self.cfg:
            return
        self._loading = True
        for key, row in self.combos.items():
            cur = self.cfg["styles"][key]
            row.set_selected(next((i for i, (v, _) in enumerate(row._opts) if v == cur), 0))
            row.set_subtitle(dict(self.CATS_SUB)[key] + " — " + self.DESC.get(cur, ""))
        self._loading = False
        clear(self.overrides)
        for pat, cat in self.cfg["styles"]["app_overrides"].items():
            row = Adw.ActionRow(title=esc(pat), subtitle=dict((c[0], c[1]) for c in self.CATS)[cat])
            row.add_suffix(icon_button("user-trash-symbolic", "Entfernen", self._remove_override, pat))
            add(self.overrides, row)

    @property
    def CATS_SUB(self):
        return [(c[0], c[2]) for c in self.CATS]

    def _changed(self, row, _pspec, key):
        if self._loading or not getattr(self, "cfg", None):
            return
        styles = dict(self.cfg["styles"])
        styles[key] = row._opts[row.get_selected()][0]
        if self.safe("config.set", {"styles": styles}, ok="Stil gespeichert") is not None:
            self.refresh()

    def _add_override(self, *_):
        pat = self.pattern.get_text().strip().lower()
        if not pat:
            return
        styles = dict(self.cfg["styles"])
        styles["app_overrides"] = {**styles["app_overrides"], pat: self.CATS[self.cat.get_selected()][0]}
        self.safe("config.set", {"styles": styles}, ok="Zuordnung gespeichert")
        self.pattern.set_text("")
        self.refresh()

    def _remove_override(self, pat):
        styles = dict(self.cfg["styles"])
        styles["app_overrides"] = {k: v for k, v in styles["app_overrides"].items() if k != pat}
        self.safe("config.set", {"styles": styles})
        self.refresh()


# --------------------------------------------------------------------------- Transforms
class TransformsPage(Page):
    title, icon = "Transforms", "emblem-synchronizing-symbolic"

    def build(self):
        self.page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Transforms",
                                 description="Text markieren, Ctrl+Super+Alt halten und „Transform &lt;Name&gt;“ sagen – "
                                             "oder den Namen direkt. Auch über das Leisten-Menü erreichbar.")
        self.name = Adw.EntryRow(title="Name")
        g.add(self.name)
        self.prompt = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8, left_margin=10,
                                   right_margin=10)
        g.add(Gtk.Frame(child=Gtk.ScrolledWindow(child=self.prompt, min_content_height=80), margin_top=8))
        btn = Adw.ButtonRow(title="Speichern", start_icon_name="document-save-symbolic")
        btn.connect("activated", self._save)
        g.add(btn)
        self.page.add(g)
        self.list = Adw.PreferencesGroup(title="Deine Transforms")
        self.page.add(self.list)
        return self.page

    def _save(self, *_):
        name = self.name.get_text().strip()
        buf = self.prompt.get_buffer()
        prompt = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        if name and prompt:
            self.safe("transforms.add", {"name": name, "prompt": prompt}, ok="Transform gespeichert")
            self.name.set_text("")
            buf.set_text("")
            self.refresh()

    def refresh(self):
        clear(self.list)
        for t in self.safe("transforms.list") or []:
            row = Adw.ActionRow(title=esc(t["name"]), subtitle=esc(t["prompt"]), subtitle_lines=2)
            row.add_suffix(icon_button("document-edit-symbolic", "Bearbeiten", self._edit, t))
            row.add_suffix(icon_button("user-trash-symbolic", "Löschen", self._remove, t["id"]))
            add(self.list, row)

    def _edit(self, t):
        self.name.set_text(t["name"])
        self.prompt.get_buffer().set_text(t["prompt"])

    def _remove(self, tid):
        self.safe("transforms.remove", {"id": tid})
        self.refresh()


# --------------------------------------------------------------------------- Notizen
class NotesPage(Page):
    title, icon = "Notizen", "accessories-text-editor-symbolic"

    def build(self):
        self.page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Scratchpad",
                                 description="Schwebender Notizblock mit Tabs – diktiere direkt hinein. "
                                             "Öffnen auch mit Ctrl+Super+N.")
        btn = Adw.ButtonRow(title="Scratchpad öffnen", start_icon_name="accessories-text-editor-symbolic")
        btn.connect("activated", lambda *_: subprocess.Popen(["whisprfake", "scratchpad"]))
        g.add(btn)
        self.page.add(g)
        self.list = Adw.PreferencesGroup(title="Notizen")
        self.page.add(self.list)
        return self.page

    def refresh(self):
        clear(self.list)
        for n in self.safe("notes.list") or []:
            row = Adw.ActionRow(title=esc(n["title"]), subtitle=f"{when(n['updated'])} · {esc(n['preview'][:120])}",
                                activatable=True)
            row.connect("activated", lambda _r, i=n["id"]: subprocess.Popen(["whisprfake", "scratchpad", "--note", str(i)]))
            row.add_suffix(icon_button("user-trash-symbolic", "Löschen", self._remove, n["id"]))
            add(self.list, row)

    def _remove(self, nid):
        self.safe("notes.delete", {"id": nid})
        self.refresh()


# --------------------------------------------------------------------------- Meetings
class MeetingsPage(Page):
    title, icon = "Meetings", "call-start-symbolic"

    def build(self):
        self.page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Notetaker",
                                 description="Nimmt Mikrofon und Systemton auf, trennt Sprecher und fasst lokal zusammen. "
                                             "Bei Zoom/Meet/Teams/Discord wird die Aufnahme automatisch vorgeschlagen.")
        self.title_row = Adw.EntryRow(title="Titel (optional)")
        g.add(self.title_row)
        self.btn = Adw.ButtonRow(title="Aufnahme starten", start_icon_name="media-record-symbolic")
        self.btn.connect("activated", self._toggle)
        g.add(self.btn)
        self.page.add(g)
        self.list = Adw.PreferencesGroup(title="Aufnahmen")
        self.page.add(self.list)
        return self.page

    def refresh(self):
        st = self.safe("meeting.status") or {}
        rec = st.get("recording")
        self.btn.set_title("Aufnahme beenden" if rec else "Aufnahme starten")
        self.btn.set_start_icon_name("media-playback-stop-symbolic" if rec else "media-record-symbolic")
        clear(self.list)
        STATUS = {"recording": "nimmt auf …", "processing": "wird verarbeitet …", "done": "fertig", "failed": "fehlgeschlagen"}
        for mt in self.safe("meeting.list") or []:
            dur = int(((mt.get("ended") or mt["started"]) - mt["started"]) / 60)
            exp = Adw.ExpanderRow(title=esc(mt["title"] or "Meeting"),
                                  subtitle=f"{when(mt['started'])} · {dur} min · {STATUS.get(mt['status'], mt['status'])}")
            if mt.get("summary"):
                lab = Gtk.Label(label=mt["summary"], wrap=True, xalign=0, selectable=True, margin_top=12,
                                margin_bottom=12, margin_start=12, margin_end=12)
                exp.add_row(lab)
            actions = Adw.ActionRow(title="Aktionen")
            if mt.get("markdown_path"):
                actions.add_suffix(icon_button("document-open-symbolic", "Markdown öffnen",
                                               lambda p: subprocess.Popen(["xdg-open", p]), mt["markdown_path"]))
            actions.add_suffix(icon_button("user-trash-symbolic", "Löschen", self._remove, mt["id"]))
            exp.add_row(actions)
            add(self.list, exp)

    def _toggle(self, *_):
        st = self.safe("meeting.status") or {}
        if st.get("recording"):
            self.safe("meeting.stop", ok="Aufnahme beendet – wird verarbeitet")
        else:
            self.safe("meeting.start", {"title": self.title_row.get_text().strip()}, ok="Aufnahme läuft")
            self.title_row.set_text("")
        GLib.timeout_add(400, lambda: (self.refresh(), False)[1])

    def _remove(self, mid):
        self.safe("meeting.delete", {"id": mid})
        self.refresh()
