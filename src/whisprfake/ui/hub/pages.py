"""Hub pages: Übersicht, Verlauf, Wörterbuch, Snippets, Styles, Transforms, Notizen, Meetings."""

from __future__ import annotations

import json
import subprocess

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

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
def _keycaps(*keys: str) -> Gtk.Box:
    box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
    for i, k in enumerate(keys):
        if i:
            plus = Gtk.Label(label="+")
            plus.add_css_class("wf-plus")
            box.append(plus)
        cap = Gtk.Label(label=k)
        cap.add_css_class("wf-keycap")
        box.append(cap)
    return box


def _label(text: str, css: str, **kw) -> Gtk.Label:
    lab = Gtk.Label(label=text, **kw)
    for c in css.split():
        lab.add_css_class(c)
    return lab


class WeekChart(Gtk.DrawingArea):
    def __init__(self):
        super().__init__(content_height=150, hexpand=True)
        self.data: list[tuple[str, int]] = []
        self.set_draw_func(self._draw)

    def set_data(self, data):
        self.data = data
        self.queue_draw()

    def _draw(self, _a, cr, w, h):
        from ..theme import colors

        c = colors()
        acc = Gdk.RGBA()
        acc.parse(c.get("accent", "#3584e4"))
        fg = self.get_color()
        n = max(1, len(self.data))
        top, bottom = 18, 22
        vmax = max([v for _, v in self.data] + [1])
        slot = w / n
        bw = min(34, slot * 0.55)
        for i, (day, v) in enumerate(self.data):
            x = i * slot + (slot - bw) / 2
            bh = max(4, (h - top - bottom) * v / vmax) if v else 4
            y = h - bottom - bh
            r = min(8, bw / 2, bh / 2)
            today = i == n - 1
            cr.set_source_rgba(acc.red, acc.green, acc.blue, 1.0 if today else 0.55 if v else 0.15)
            cr.new_sub_path()
            cr.arc(x + bw - r, y + r, r, -1.5708, 0)
            cr.line_to(x + bw, y + bh)
            cr.line_to(x, y + bh)
            cr.arc(x + r, y + r, r, 3.1416, 4.7124)
            cr.close_path()
            cr.fill()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.9 if today else 0.5)
            cr.select_font_face("Sans")
            cr.set_font_size(11)
            ext = cr.text_extents(day)
            cr.move_to(i * slot + (slot - ext.width) / 2, h - 5)
            cr.show_text(day)
            if v:
                txt = str(v)
                ext = cr.text_extents(txt)
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.7)
                cr.move_to(i * slot + (slot - ext.width) / 2, y - 5)
                cr.show_text(txt)


class HomePage(Page):
    title, icon = "Übersicht", "go-home-symbolic"

    def build(self):
        outer = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=900, tightening_threshold=700)
        outer.set_child(clamp)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22, margin_top=26, margin_bottom=30,
                      margin_start=26, margin_end=26)
        clamp.set_child(col)

        # hero
        hero = Gtk.Box(spacing=24)
        hero.add_css_class("wf-hero")
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True, valign=Gtk.Align.CENTER)
        self.greet = _label("", "wf-hero-title", xalign=0)
        self.sub = _label("", "wf-hero-sub", xalign=0, wrap=True)
        left.append(self.greet)
        left.append(self.sub)
        hero.append(left)
        keys = Gtk.Grid(row_spacing=8, column_spacing=12, valign=Gtk.Align.CENTER)
        for row, (caps, hint) in enumerate([(("Ctrl", "Super"), "halten zum Diktieren"),
                                            (("Ctrl", "Super", "Space"), "freihändig"),
                                            (("Ctrl", "Super", "Alt"), "Befehl / Frage"),
                                            (("Esc",), "abbrechen")]):
            kc = _keycaps(*caps)
            kc.set_halign(Gtk.Align.END)
            keys.attach(kc, 0, row, 1, 1)
            keys.attach(_label(hint, "wf-hint", xalign=0), 1, row, 1, 1)
        hero.append(keys)
        col.append(hero)

        # stat cards
        grid = Gtk.Grid(column_spacing=14, row_spacing=14, column_homogeneous=True)
        self.cards = {}
        for i, (key, label, icon) in enumerate([
                ("total_words", "Wörter diktiert", "document-edit-symbolic"),
                ("wpm", "Wörter pro Minute", "speedometer-symbolic"),
                ("streak_days", "Tage in Folge", "starred-symbolic"),
                ("minutes_saved", "Minuten gespart", "alarm-symbolic")]):
            card = Gtk.Box(spacing=14)
            card.add_css_class("card")
            card.add_css_class("wf-stat")
            ic = Gtk.Image(icon_name=icon, pixel_size=18, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            icb = Gtk.Box(valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
            icb.set_size_request(34, 34)
            icb.add_css_class("wf-stat-icon")
            ic.set_hexpand(True)
            icb.append(ic)
            card.append(icb)
            txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            v = _label("–", "wf-stat-value", xalign=0)
            txt.append(v)
            txt.append(_label(label, "wf-stat-label", xalign=0))
            card.append(txt)
            self.cards[key] = v
            grid.attach(card, i % 4, i // 4, 1, 1)
        col.append(grid)

        # week chart
        chart_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        chart_card.add_css_class("card")
        chart_card.add_css_class("wf-card")
        chart_card.append(_label("Letzte 7 Tage", "wf-section", xalign=0))
        self.chart = WeekChart()
        chart_card.append(self.chart)
        col.append(chart_card)

        # recent + apps
        two = Gtk.Box(spacing=14, homogeneous=True)
        rc = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        rc.add_css_class("card")
        rc.add_css_class("wf-card")
        rc.append(_label("Zuletzt diktiert", "wf-section", xalign=0))
        self.recent = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        rc.append(self.recent)
        ac = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        ac.add_css_class("card")
        ac.add_css_class("wf-card")
        ac.append(_label("Wo du diktierst", "wf-section", xalign=0))
        self.apps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        ac.append(self.apps)
        two.append(rc)
        two.append(ac)
        col.append(two)
        self.footer = _label("", "wf-recent-meta", xalign=0.5)
        col.append(self.footer)
        return outer

    def refresh(self):
        import datetime as dt

        real = GLib.get_real_name() or ""
        name = (real if real and real != "Unknown" else GLib.get_user_name()).split(" ")[0].capitalize()
        h = dt.datetime.now().hour
        hello = "Gute Nacht" if h < 5 else "Guten Morgen" if h < 11 else "Hallo" if h < 18 else "Guten Abend"
        self.greet.set_label(f"{hello}{', ' + name if name and name != 'Unknown' else ''}")
        stats = self.safe("stats") or {}
        today = stats.get("today_words", 0)
        self.sub.set_label(f"Heute {today} Wörter diktiert." if today else
                           "Heute noch nichts diktiert – halte Ctrl + Super und leg los.")
        for k, lab in self.cards.items():
            v = stats.get(k, 0)
            lab.set_label(f"{v:,}".replace(",", ".") if isinstance(v, int) else str(v).replace(".", ","))
        self.chart.set_data(stats.get("last7", []))
        clear(self.recent)
        rows = [r for r in (self.safe("history", {"limit": 12}) or []) if r.get("cleaned")][:5]
        if not rows:
            self.recent.append(_label("Noch keine Diktate", "wf-empty", xalign=0))
        for r in rows:
            b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            b.append(_label(r["cleaned"], "wf-recent-text", xalign=0, wrap=True, lines=2,
                            ellipsize=3, max_width_chars=40))
            b.append(_label(f"{when(r['ts'])} · {r.get('app_class') or ''}", "wf-recent-meta", xalign=0))
            self.recent.append(b)
        clear(self.apps)
        top = stats.get("top_apps", [])
        total = sum(w for _, w in top) or 1
        if not top:
            self.apps.append(_label("Noch keine Daten", "wf-empty", xalign=0))
        for app, words in top:
            b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            line = Gtk.Box()
            line.append(_label(app, "wf-recent-text", xalign=0, hexpand=True))
            line.append(_label(f"{words} Wörter", "wf-recent-meta"))
            b.append(line)
            pb = Gtk.ProgressBar(fraction=words / total)
            pb.add_css_class("wf-app-bar")
            b.append(pb)
            self.apps.append(b)
        st = self.safe("status")
        self.footer.set_label(f"Spracherkennung {st['asr']} · Bereinigung {st['llm']} · alles lokal" if st else
                              "Dienst nicht erreichbar")


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
        box.append(self.page)
        return box

    def refresh(self):
        import datetime as dt

        for g in getattr(self, "groups", []):
            self.page.remove(g)
        self.groups = []
        rows = self.safe("history", {"limit": 300, "search": self.search.get_text()}) or []
        if not rows:
            g = Adw.PreferencesGroup()
            g.add(Adw.StatusPage(title="Noch nichts hier", icon_name="audio-input-microphone-symbolic",
                                 description="Halte Ctrl + Super und sprich – deine Diktate erscheinen hier."))
            self.page.add(g)
            self.groups.append(g)
            return
        today = dt.date.today()
        current_day, group = None, None
        for r in rows:
            d = dt.date.fromtimestamp(r["ts"])
            if d != current_day:
                current_day = d
                title = "Heute" if d == today else "Gestern" if d == today - dt.timedelta(days=1) \
                    else d.strftime("%A, %d.%m.%Y")
                group = Adw.PreferencesGroup(title=title)
                self.page.add(group)
                self.groups.append(group)
            text = r.get("cleaned") or r.get("raw") or ""
            meta = [dt.datetime.fromtimestamp(r["ts"]).strftime("%H:%M"), r.get("app_class") or ""]
            if r.get("mode") in ("command", "answer"):
                meta.append("Befehl")
            if r["status"] not in ("inserted",):
                meta.append(self.STATUS.get(r["status"], r["status"]))
            if r.get("latency_ms"):
                meta.append(f"{r['latency_ms']} ms")
            exp = Adw.ExpanderRow(title=esc(text[:180]) or "<i>(leer)</i>", subtitle=" · ".join(m for m in meta if m))
            exp.set_title_lines(3)
            icon = "mail-reply-sender-symbolic" if r.get("mode") in ("command", "answer") else "audio-input-microphone-symbolic"
            exp.add_prefix(Gtk.Image(icon_name=icon, opacity=0.5))
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
            group.add(exp)

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
        builtin = Adw.PreferencesGroup(title="Mitgelieferte Wörterbücher")
        r1 = Adw.ActionRow(title="Rechtschreibung Deutsch + Englisch",
                           subtitle="Hunspell (LibreOffice) – erkennt verhörte Wörter und gleicht sie mit deinem Wörterbuch ab")
        r1.add_prefix(Gtk.Image(icon_name="tools-check-spelling-symbolic"))
        builtin.add(r1)
        r2 = Adw.ActionRow(title="Anglizismen in Duden-Schreibweise",
                           subtitle="„email“ → E-Mail, „know how“ → Know-how, „home office“ → Homeoffice …",
                           activatable=True)
        r2.add_prefix(Gtk.Image(icon_name="accessories-dictionary-symbolic"))
        r2.add_suffix(Gtk.Label(label="eigene ergänzen", css_classes=["dim-label"]))
        r2.add_suffix(Gtk.Image(icon_name="document-edit-symbolic"))
        r2.connect("activated", lambda *_: self._edit_anglicisms())
        builtin.add(r2)
        self.page.add(builtin)
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

    def _edit_anglicisms(self):
        from ...pipeline.lexicon import USER_ANGLICISMS

        if not USER_ANGLICISMS.exists():
            USER_ANGLICISMS.parent.mkdir(parents=True, exist_ok=True)
            USER_ANGLICISMS.write_text("# Eigene Anglizismen / Schreibweisen, eine pro Zeile (Nomen groß, Verben klein).\n"
                                       "# Beispiel:\n# Work-Life-Balance\n# Call-Center\n")
        subprocess.Popen(["xdg-open", str(USER_ANGLICISMS)])
        self.toast("Nach dem Speichern: Einstellungen › Dienst neu starten")

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
