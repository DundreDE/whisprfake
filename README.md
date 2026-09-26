# whisprfake

Wispr Flow für Arch Linux / Omarchy. Taste halten, sprechen, loslassen, und sauber formulierter Text steht im
aktiven Textfeld. **Alle KI-Schritte laufen lokal auf deiner GPU**: Spracherkennung, Bereinigung, Befehle und
Meeting-Zusammenfassungen. Es gibt keine Cloud und kein Konto.

## Bedienung

| Aktion | Standard |
|---|---|
| Diktieren (halten) | **Ctrl + Super** |
| Freihändig an/aus | **Ctrl + Super + Leertaste** oder Doppeltipp auf Ctrl + Super |
| Command Mode (halten) | **Ctrl + Super + Alt** |
| Abbrechen | **Esc** |
| Letztes Diktat erneut einfügen | Ctrl + Super + Alt + V |
| Hub (Verlauf, Wörterbuch, Einstellungen) | **Super + Alt + W** oder Leisten-Symbol (Rechtsklick) |
| Scratchpad | **Super + Alt + N** |
| Omarchy-Menü › Diktat | Hub, Scratchpad, Meeting aufnehmen, Transforms |

Das Background-Menü von Omarchy liegt jetzt auf **Ctrl + Super + Shift + B**.

## Funktionen

- **Bereinigung wie Wispr:** Füllwörter („äh“, „um“) fliegen raus. Selbstkorrekturen werden aufgelöst
  („um zwei, nein um drei“ → „um 3“). Aufzählungen werden zu Listen. Satzzeichen per Sprache („Komma“,
  „Fragezeichen“, „neue Zeile“, „neuer Absatz“). „drück Enter“ / „press enter“ schickt die Nachricht ab.
- **Fachbegriffe:** Ein vorbefülltes Tech-Wörterbuch plus eigene Einträge, auch mit „klingt wie“. Die
  Korrektur geht über Lautähnlichkeit (Kölner Phonetik / Metaphone) und das LLM-Glossar. `camel case`,
  `snake case` und CLI-Flags (`minus m` → `-m`) werden umgesetzt.
- **Deutsch und Englisch gemischt:** Die Sprache wird automatisch erkannt, es wird nie übersetzt.
- **Styles pro App-Kategorie:** Private Chats, Arbeits-Chat, E-Mail und Rest, jeweils mit Formell / Locker /
  sehr locker / Begeistert. Web-Apps werden über Titel und URL erkannt.
- **Kontext:** Die App, der Text vor dem Cursor (für Kleinschreibung mitten im Satz) und Namen auf dem
  Bildschirm kommen per AT-SPI. Passwortfelder werden nie gelesen. Abschaltbar.
- **Command Mode:**
  - Text markieren und eine Anweisung sprechen („mach das kürzer“, „auf Englisch“).
  - „Such auf Google/Perplexity …“ öffnet den Browser.
  - Fragen ohne Markierung beantwortet das lokale LLM im Popup (Einfügen/Kopieren).
  - „Füge X zum Wörterbuch hinzu“ trägt einen Begriff ein.
- **Transforms:** eigene Prompts für markierten Text, per Sprache oder Menü.
- **Snippets:** Du sprichst einen Auslöser, eingefügt wird der hinterlegte Textblock.
- **Flow Bar:** Animierte Pille im Omarchy-Theme, nur während der Aufnahme sichtbar. Sie zeigt eine
  Waveform, beim Verarbeiten eine laufende Welle, freihändig einen Timer mit Stopp/Abbrechen.
- **Hub:**
  - Statistiken (Wörter, WPM, Serie, gesparte Zeit)
  - Verlauf mit Audio (14 Tage), Neu verarbeiten und Roh-/bereinigt-Vergleich
  - Wörterbuch mit Auto-Learn-Vorschlägen, Snippets, Styles, Transforms, Notizen, Meetings, Einstellungen
- **Scratchpad:** schwebender Notizblock mit Tabs, Autosave und Versionsverlauf.
- **Notetaker:**
  - Nimmt Mikrofon und Systemton auf, trennt die Sprecher und fasst lokal zusammen.
  - Speichert das Ergebnis als Markdown in `~/Documents/Meetings/`.
  - Schlägt die Aufnahme automatisch vor, wenn Zoom, Meet, Teams oder Discord dein Mikro nutzen.
- **Robust:** Das Audio wird vor der Verarbeitung gespeichert. Diktate, die ein Absturz unterbrochen hat, werden
  beim nächsten Start wiederhergestellt. Fällt das LLM aus oder antwortet statt zu bereinigen, wird der
  regelbasiert bereinigte Text eingefügt.

## Technik

```
evdev (Ctrl+Super) ─▶ whisprfake-daemon (Python, systemd --user)
                        ├─ Mikro 16 kHz + Silero-VAD → Parakeet TDT v3 (whisper.cpp/Vulkan, ~0,1 s)
                        ├─ Kontext: hyprctl + AT-SPI
                        ├─ Regeln → Qwen3 4B (Ollama, ~0,2 s) → Guard → Wörterbuch/Snippets
                        ├─ Einfügen: Zwischenablage + Hyprland send_key_state, danach Clipboard zurück
                        └─ SQLite, Unix-Socket-IPC ─▶ omarchy-shell-Plugin (QML) · Hub/Scratchpad (GTK4/libadwaita)
```

Alternative Engines (Einstellungen › Spracherkennung): Whisper large-v3-turbo und Qwen3-ASR 1.7B, beide auf
der GPU, sowie Parakeet auf der CPU. Die Modelle wählst du in den Einstellungen unter KI-Bereinigung.

## Installation

```sh
git clone https://github.com/DundreDE/whisprfake ~/DEV/whisprfake && cd ~/DEV/whisprfake
./packaging/install.sh          # oder --all für die alternativen Engines
```

Danach einmal ab- und wieder anmelden (Gruppe `input` für den Hotkey). Alternativ als Arch-Paket:
`cd packaging/arch && makepkg -si && whisprfake-setup`.

## Nützliches

```sh
whisprfake ctl status                 # Dienststatus
whisprfake ctl stats                  # Statistik
whisprfake ctl dictionary.add '{"term":"Paperless-ngx","sounds_like":["paperless ngx"]}'
whisprfake transcribe datei.wav       # Datei transkribieren + bereinigen
journalctl --user -u whisprfake -f    # Log
uv run pytest                         # Tests
uv run python bench/run_cleanup.py    # Bereinigungs-Benchmark
```

Konfiguration: `~/.config/whisprfake/config.toml`. Daten: `~/.local/share/whisprfake/`.
