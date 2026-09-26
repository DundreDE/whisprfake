import subprocess

from whisprfake.pipeline import filerefs


def repo(tmp_path):
    for f in ["src/lib/auth.ts", "README.md", "docs/README.md", "src/services/userService.ts", "package.json",
              "src/config.ts", "src/components/LoginForm.tsx", "server/auth.ts"]:
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    return filerefs.FileIndex(tmp_path).build()


def tag(text, idx, known=lambda w: w.lower() in {"config", "auth"}):
    t, m = filerefs.protect(text, idx, "@", known)
    return filerefs.expand(t, m)


def test_mentions(tmp_path):
    idx = repo(tmp_path)
    # two auth.ts: without a hint the shallower one wins, a mentioned folder decides otherwise
    assert tag("Schau dir mal auth.ts an", idx) == "Schau dir mal @server/auth.ts an"
    assert tag("schau dir mal die auth punkt ts in lib an", idx) == "schau dir mal die @src/lib/auth.ts in lib an"
    assert tag("check the auth dot ts in the server folder", idx) == "check the @server/auth.ts in the server folder"
    assert tag("Lies die readme und die package json", idx) == "Lies die @README.md und die @package.json"
    assert tag("In der User Service Datei ist ein Bug", idx) == "In der @src/services/userService.ts Datei ist ein Bug"
    assert tag("Das Login Form tsx ist kaputt", idx) == "Das @src/components/LoginForm.tsx ist kaputt"


def test_no_false_positives(tmp_path):
    idx = repo(tmp_path)
    # "config" and "auth" are ordinary words: only tagged with an extension or a "Datei/file" next to them
    assert tag("Die config ist falsch", idx) == "Die config ist falsch"
    assert tag("Die config Datei ist falsch", idx) == "Die @src/config.ts Datei ist falsch"
    assert tag("Wir brauchen bessere auth", idx) == "Wir brauchen bessere auth"


def test_common_words_and_compounds(tmp_path):
    idx = repo(tmp_path)
    assert tag("Ich finde die config gut so, aber in der config ts fehlt ein Default", idx) == \
        "Ich finde die config gut so, aber in der @src/config.ts fehlt ein Default"
    t, m = filerefs.protect("In der ⟦X⟧", idx)
    assert filerefs.expand("In der ⟦F0⟧-Datei", {"⟦F0⟧": "@a/b.py"}) == "In der @a/b.py Datei"
