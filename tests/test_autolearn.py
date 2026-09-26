from whisprfake.store.autolearn import suggestions


def test_detects_name_fix():
    ins = "Hallo Jacob, wir nutzen Super Base für das Backend."
    cur = "Vorheriger Text. Hallo Jakob, wir nutzen Supabase für das Backend. Danach mehr."
    got = suggestions(ins, cur, set())
    assert ("Jacob", "Jakob") in got
    assert ("Super Base", "Supabase") in got


def test_ignores_rewrites_and_known_terms():
    ins = "Ich komme morgen um drei."
    cur = "Ich komme übermorgen am Abend vorbei."
    assert suggestions(ins, cur, set()) == []
    assert suggestions("Hallo Jacob", "Hallo Jakob", {"jakob"}) == []


def test_unchanged_text():
    assert suggestions("Alles gut hier.", "Alles gut hier.", set()) == []
