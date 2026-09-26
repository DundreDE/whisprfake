"""Local SQLite store: history, dictionary, snippets, transforms, notes (scratchpad), meetings."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from ..config import DB_PATH
from ..pipeline.dictionary import Term
from ..pipeline.snippets import Snippet

MIGRATIONS = [
    """
    CREATE TABLE dictations (
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,
        mode TEXT NOT NULL,              -- dictate | command | answer | transform
        status TEXT NOT NULL,            -- pending | inserted | cancelled | failed | empty
        app_class TEXT, app_title TEXT, category TEXT, style TEXT, language TEXT,
        raw TEXT, cleaned TEXT, instruction TEXT,
        asr_engine TEXT, llm_model TEXT, guard TEXT,
        audio_path TEXT, duration_s REAL, words INTEGER,
        asr_ms INTEGER, llm_ms INTEGER, latency_ms INTEGER
    );
    CREATE INDEX dictations_ts ON dictations(ts);
    CREATE TABLE dictionary (
        id INTEGER PRIMARY KEY, term TEXT UNIQUE NOT NULL, sounds_like TEXT DEFAULT '[]',
        starred INTEGER DEFAULT 0, uses INTEGER DEFAULT 0, created REAL
    );
    CREATE TABLE dictionary_suggestions (
        id INTEGER PRIMARY KEY, term TEXT NOT NULL, heard TEXT, context TEXT, created REAL,
        status TEXT DEFAULT 'new'        -- new | accepted | dismissed
    );
    CREATE TABLE snippets (
        id INTEGER PRIMARY KEY, trigger TEXT UNIQUE NOT NULL, text TEXT NOT NULL, uses INTEGER DEFAULT 0, created REAL
    );
    CREATE TABLE transforms (
        id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, prompt TEXT NOT NULL, created REAL
    );
    CREATE TABLE notes (
        id INTEGER PRIMARY KEY, title TEXT, body TEXT DEFAULT '', created REAL, updated REAL,
        position INTEGER DEFAULT 0, archived INTEGER DEFAULT 0
    );
    CREATE TABLE note_versions (
        id INTEGER PRIMARY KEY, note_id INTEGER REFERENCES notes(id) ON DELETE CASCADE, body TEXT, ts REAL
    );
    CREATE TABLE meetings (
        id INTEGER PRIMARY KEY, title TEXT, started REAL, ended REAL, app TEXT, audio_path TEXT,
        segments TEXT DEFAULT '[]', speakers TEXT DEFAULT '{}', summary TEXT, markdown_path TEXT,
        status TEXT DEFAULT 'recording'  -- recording | processing | done | failed
    );
    """,
    """
    INSERT INTO transforms(name, prompt, created) VALUES
      ('Kürzer', 'Make the text more concise without losing information. Keep the language.', strftime('%s','now')),
      ('Als E-Mail', 'Rewrite the text as a polite, well-structured email. Keep the language.', strftime('%s','now')),
      ('Auf Englisch', 'Translate the text to natural English.', strftime('%s','now')),
      ('Auf Deutsch', 'Translate the text to natural German.', strftime('%s','now')),
      ('Stichpunkte', 'Turn the text into a concise bullet list. Keep the language.', strftime('%s','now')),
      ('Rechtschreibung', 'Fix spelling, grammar and punctuation only. Change nothing else.', strftime('%s','now'));
    """,
]


class Store:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.lock = threading.RLock()
        self._migrate()

    def _migrate(self) -> None:
        with self.lock:
            v = self.db.execute("PRAGMA user_version").fetchone()[0]
            for i, sql in enumerate(MIGRATIONS[v:], start=v + 1):
                self.db.executescript("BEGIN;" + sql + f";PRAGMA user_version={i};COMMIT;")

    def q(self, sql: str, args: tuple | dict = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def x(self, sql: str, args: tuple | dict = ()) -> int:
        with self.lock:
            cur = self.db.execute(sql, args)
            return cur.lastrowid or cur.rowcount

    # -- dictations -------------------------------------------------------
    def add_dictation(self, **f) -> int:
        f.setdefault("ts", time.time())
        cols = ",".join(f)
        return self.x(f"INSERT INTO dictations({cols}) VALUES ({','.join('?' * len(f))})", tuple(f.values()))

    def update_dictation(self, id: int, **f) -> None:
        if f:
            self.x(f"UPDATE dictations SET {','.join(f'{k}=?' for k in f)} WHERE id=?", (*f.values(), id))

    def history(self, limit: int = 200, search: str = "") -> list[dict]:
        if search:
            return self.q("SELECT * FROM dictations WHERE (cleaned LIKE ? OR raw LIKE ?) ORDER BY ts DESC LIMIT ?",
                          (f"%{search}%", f"%{search}%", limit))
        return self.q("SELECT * FROM dictations ORDER BY ts DESC LIMIT ?", (limit,))

    def last_inserted(self) -> dict | None:
        r = self.q("SELECT * FROM dictations WHERE status='inserted' AND mode='dictate' ORDER BY ts DESC LIMIT 1")
        return r[0] if r else None

    def pending_recovery(self) -> list[dict]:
        return self.q("SELECT * FROM dictations WHERE status='pending' AND audio_path IS NOT NULL ORDER BY ts")

    # -- dictionary / snippets -------------------------------------------
    def terms(self) -> list[Term]:
        return [Term(r["term"], json.loads(r["sounds_like"] or "[]"), bool(r["starred"]))
                for r in self.q("SELECT * FROM dictionary ORDER BY starred DESC, uses DESC, term")]

    def add_term(self, term: str, sounds_like: list[str] | None = None, starred: bool = False) -> None:
        self.x("INSERT INTO dictionary(term, sounds_like, starred, created) VALUES (?,?,?,?) "
               "ON CONFLICT(term) DO UPDATE SET sounds_like=excluded.sounds_like, starred=excluded.starred",
               (term.strip(), json.dumps(sounds_like or []), int(starred), time.time()))

    def snippets(self) -> list[Snippet]:
        return [Snippet(r["trigger"], r["text"]) for r in self.q("SELECT * FROM snippets ORDER BY trigger")]

    def add_suggestion(self, term: str, heard: str, context: str = "") -> None:
        if self.q("SELECT 1 FROM dictionary WHERE term=? COLLATE NOCASE", (term,)):
            return
        if self.q("SELECT 1 FROM dictionary_suggestions WHERE term=? AND status!='accepted'", (term,)):
            return
        self.x("INSERT INTO dictionary_suggestions(term, heard, context, created) VALUES (?,?,?,?)",
               (term, heard, context[:300], time.time()))

    # -- insights -----------------------------------------------------------
    def stats(self) -> dict:
        now = time.time()
        day = now - (now % 86400)
        rows = self.q("SELECT ts, words, duration_s, app_class FROM dictations WHERE status='inserted' AND mode='dictate'")
        total_words = sum(r["words"] or 0 for r in rows)
        spoken_s = sum(r["duration_s"] or 0 for r in rows)
        today = sum(r["words"] or 0 for r in rows if r["ts"] >= day)
        week = sum(r["words"] or 0 for r in rows if r["ts"] >= now - 7 * 86400)
        days = sorted({int(r["ts"] // 86400) for r in rows}, reverse=True)
        streak, d = 0, int(now // 86400)
        for x in days:
            if x == d - streak:
                streak += 1
            elif x < d - streak:
                break
        apps: dict[str, int] = {}
        for r in rows:
            apps[r["app_class"] or "?"] = apps.get(r["app_class"] or "?", 0) + (r["words"] or 0)
        import datetime as _dt

        today_d = _dt.date.today()
        per_day = {}
        for r in rows:
            d = _dt.date.fromtimestamp(r["ts"])
            per_day[d] = per_day.get(d, 0) + (r["words"] or 0)
        names = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
        last7 = [(names[(today_d - _dt.timedelta(days=i)).weekday()], per_day.get(today_d - _dt.timedelta(days=i), 0))
                 for i in range(6, -1, -1)]
        return {
            "last7": last7,
            "total_words": total_words, "today_words": today, "week_words": week,
            "wpm": round(total_words / (spoken_s / 60), 1) if spoken_s > 30 else 0,
            "dictations": len(rows), "streak_days": streak,
            "top_apps": sorted(apps.items(), key=lambda kv: -kv[1])[:5],
            # typing ~40 wpm → minutes saved
            "minutes_saved": round(total_words / 40 - spoken_s / 60, 1),
        }
