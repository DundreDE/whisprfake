"""Tiny stopword-based German/English detector (Parakeet doesn't report the language)."""

import re

_DE = set("""der die das und ist nicht ich du wir ihr sie es ein eine einen dem den des mit für auf zu von
im in bei nach auch noch nur schon doch mal kann kannst können wie was wo wann warum dass weil wenn ob oder aber
bin bist sind war hab habe hast hat haben wird werden mir mich dir dich uns euch ja nein bitte danke""".split())
_EN = set("""the and is are not i you we they it a an to of for on in at with this that what where when why
because if or but am was were have has had will would can could should me my your our do does did yes no please
thanks just so um uh""".split())


def detect(text: str) -> str:
    words = re.findall(r"[a-zäöüß']+", text.lower())
    de = sum(w in _DE for w in words) + 2 * sum(any(c in w for c in "äöüß") for w in words)
    en = sum(w in _EN for w in words)
    if de == en == 0:
        return "unknown"
    if de >= 2 * max(en, 1) or (de and not en):
        return "German"
    if en >= 2 * max(de, 1) or (en and not de):
        return "English"
    return "mixed German/English"
