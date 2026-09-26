from whisprfake.context.categories import AppInfo, categorize
from whisprfake.pipeline import dictionary, guard, rules, snippets


def test_submit_command():
    assert rules.extract_submit("Bin gleich da, drück Enter") == ("Bin gleich da", True)
    assert rules.extract_submit("see you soon press enter.") == ("see you soon", True)
    assert rules.extract_submit("Enter ist eine Taste") == ("Enter ist eine Taste", False)


def test_breaks_roundtrip():
    t = rules.protect_breaks("Hallo Anna, neue Zeile wie geht's neuer Absatz Gruß Jakob")
    assert rules.NL in t and rules.PARA in t
    assert rules.restore_placeholders(t) == "Hallo Anna,\nWie geht's\n\nGruß Jakob"


def test_spoken_punctuation():
    assert rules.spoken_punctuation("Kommst du heute Fragezeichen") == "Kommst du heute?"
    assert rules.spoken_punctuation("I can't wait exclamation point") == "I can't wait!"
    assert rules.spoken_punctuation("Der Punkt ist wichtig Punkt") == "Der Punkt ist wichtig."
    assert rules.spoken_punctuation("Hallo Komma wie geht's") == "Hallo, wie geht's"


def test_fillers():
    assert rules.remove_fillers("Also ähm ich wollte äh sagen") == "Also ich wollte sagen"
    assert rules.remove_fillers("umm I think uh yes") == "I think yes"
    assert rules.remove_fillers("Die Umfrage um drei") == "Die Umfrage um drei"  # German preposition stays


def test_guard_rejects_answers_and_accepts_cleanup():
    src = "was ist eigentlich die Hauptstadt von Peru"
    ok, _ = guard.check(src, "Die Hauptstadt von Peru ist Lima, eine Stadt an der Pazifikküste mit 10 Millionen Einwohnern.")
    assert not ok
    ok, _ = guard.check(src, "Was ist eigentlich die Hauptstadt von Peru?")
    assert ok
    ok, why = guard.check("treffen wir uns um zwei äh nein um drei", "Treffen wir uns um drei.")
    assert ok, why
    assert not guard.check("Text ⟦S0⟧ hier", "Text hier")[0]


def test_guard_strips_preamble():
    assert guard.strip_wrapping('Here is the cleaned text: "Hello there."') == "Hello there."
    assert guard.strip_wrapping("<think>\n</think>\nHallo.") == "Hallo."


def test_snippets():
    sn = [snippets.Snippet("mein kalender link", "https://cal.com/jakob")]
    t, m = snippets.protect("Hier ist Mein Kalender-Link. Bis dann", sn)
    assert "⟦S0⟧" in t
    assert snippets.expand(t, m) == "Hier ist https://cal.com/jakob Bis dann"


def test_dictionary_correction():
    terms = [dictionary.Term("Supabase"), dictionary.Term("Hyprland"), dictionary.Term("Omarchy")]
    assert dictionary.correct("Wir nutzen super base und hyprland", terms) == "Wir nutzen Supabase und Hyprland"
    assert dictionary.correct("Ich mag Äpfel", terms) == "Ich mag Äpfel"
    t2 = [dictionary.Term("Kubernetes", sounds_like=["cooper netties"])]
    assert dictionary.correct("deploy on cooper netties", t2) == "deploy on Kubernetes"


def test_koelner():
    assert dictionary.koelner("Müller") == dictionary.koelner("Mueller")
    assert dictionary.koelner("Wikipedia") == "3412"


def test_categories():
    assert categorize(AppInfo(wm_class="Slack")) == "work"
    assert categorize(AppInfo(wm_class="firefox", title="Inbox - Gmail — Mozilla Firefox")) == "email"
    assert categorize(AppInfo(wm_class="chromium", url="https://web.whatsapp.com/")) == "personal"
    assert categorize(AppInfo(wm_class="Alacritty", title="nvim")) == "other"
    assert categorize(AppInfo(wm_class="firefox", title="Periodic elements table")) == "other"


def test_dictionary_split_words():
    t = [dictionary.Term("PostgreSQL")]
    for heard in ["Post QSQL.", "Post Creed SQL!", "Post free SQL.", "Postgre SQL."]:
        assert dictionary.correct(heard, t).startswith("PostgreSQL"), heard
    assert dictionary.correct("Post für alle.", t) == "Post für alle."
    assert dictionary.correct("Wir posten es später.", t) == "Wir posten es später."


def test_dictionary_no_false_positives():
    from whisprfake.store.seed import TECH_TERMS

    T = [dictionary.Term(t, s) for t, s in TECH_TERMS]
    for s in ["The cursor is blinking in the editor.", "Ich habe Rast gemacht und dann weiter.",
              "Der Docker Container startet nicht.", "Ich gehe heute noch zur Post.", "How do you react to that?"]:
        assert dictionary.correct(s, T) == s, s


def test_format_lists_ordinals():
    t = "Hallo, ich habe eine Liste an Sachen, die geändert werden müssen. Erstens, ich bin CUDA. Zweitens, du bist CUDA. Drittens, alle werden CUDA."
    assert rules.format_lists(t) == ("Hallo, ich habe eine Liste an Sachen, die geändert werden müssen:\n"
                                     "- Ich bin CUDA\n- Du bist CUDA\n- Alle werden CUDA")
    t2 = "The plan: first, update the docs, second, ship it and third, tell the team."
    assert rules.format_lists(t2) == "The plan:\n- Update the docs\n- Ship it\n- Tell the team"
    assert rules.format_lists("Das war das erste Mal, dass ich zweitens gesagt habe.") == \
        "Das war das erste Mal, dass ich zweitens gesagt habe."


def test_format_lists_bullet_words():
    t = "Morgen muss ich Stichpunkt einkaufen gehen Stichpunkt Steuer machen Stichpunkt Oma anrufen"
    assert rules.format_lists(t) == "Morgen muss ich:\n- Einkaufen gehen\n- Steuer machen\n- Oma anrufen"


def test_set_bullets():
    assert rules.set_bullets("Liste:\n- A\n* B\n• C", "•") == "Liste:\n• A\n• B\n• C"


def test_anglicisms():
    from whisprfake.pipeline.lexicon import Lexicon

    L = Lexicon()
    assert L.normalize_anglicisms("die email mit dem know how") == "die E-Mail mit dem Know-how"
    assert L.normalize_anglicisms("Cool, okay") == "Cool, okay"
    assert L.normalize_anglicisms('Mach mal git commit -m "fix typo" und dann git push.') == \
        'Mach mal git commit -m "fix typo" und dann git push.'
    assert L.normalize_anglicisms("Der commit und das update") == "Der Commit und das Update"


def test_colon_lists():
    t = "Für das Projekt brauchen wir noch folgende Punkte: ein Login mit Google, eine Suchfunktion, einen Dark Mode und Export als PDF."
    assert rules.format_lists(t) == ("Für das Projekt brauchen wir noch folgende Punkte:\n- Ein Login mit Google\n"
                                     "- Eine Suchfunktion\n- Einen Dark Mode\n- Export als PDF")
    t2 = "The pros are: it's fast, it's local and it's free. The cons are: it needs a GPU and setup takes time."
    assert rules.format_lists(t2) == ("The pros are:\n- It's fast\n- It's local\n- It's free\n\nThe cons are:\n"
                                      "- It needs a GPU\n- Setup takes time")
    assert rules.format_lists("Ich brauche noch Milch, Eier und Brot.") == "Ich brauche noch Milch, Eier und Brot."
    assert rules.format_lists("Wichtig: morgen früh anrufen.") == "Wichtig: morgen früh anrufen."


def test_keep_anglicisms():
    assert rules.keep_anglicisms("kannst du die readme updaten", "Kannst du die Readme aktualisieren?") == \
        "Kannst du die Readme updaten?"
    assert rules.keep_anglicisms("bitte aktualisieren", "Bitte aktualisieren.") == "Bitte aktualisieren."
