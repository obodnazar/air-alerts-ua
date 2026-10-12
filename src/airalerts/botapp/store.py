"""SQLite: subscribers, their settings, their raions (up to 3), and a send log (no duplicates).

Only Telegram chat ids and chosen settings are stored. The file lives in data/bot/ (git-ignored).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path("data/bot/bot.sqlite")
MAX_UNITS = 3
ALLOWED = {
    "cp": {9, 10, 11, 12},                       # school control point (hour)
    "morning": {"alert", "always"},              # morning message only on alert, or always
    "evening": {"20:00", "20:30", "21:00"},      # when the evening forecast arrives
    "style": {"careful", "balanced", "bold"},    # how readily we advise a remote day
    "banner": {0, 1},
    "active": {0, 1},
}


class Store:
    def __init__(self, path: Path | str = DB):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # autocommit: every statement is its own transaction, so no early return can leave a write lock open
        # (the first version locked the database for the worker thread; audit, 12 Oct)
        self.db = sqlite3.connect(str(path), isolation_level=None, timeout=30)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS subs (chat_id INTEGER PRIMARY KEY, unit TEXT, cp INTEGER DEFAULT 10,
                morning TEXT DEFAULT 'alert', active INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS units (chat_id INTEGER, unit TEXT, PRIMARY KEY (chat_id, unit));
            CREATE TABLE IF NOT EXISTS sent (chat_id INTEGER, kind TEXT, day TEXT, PRIMARY KEY (chat_id, kind, day));
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
        """)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(subs)")}
        for col, ddl in (("evening", "TEXT DEFAULT '20:00'"), ("style", "TEXT DEFAULT 'balanced'"),
                         ("banner", "INTEGER DEFAULT 1"), ("paused_until", "TEXT")):
            if col not in cols:  # migrate databases created by the first version
                self.db.execute(f"ALTER TABLE subs ADD COLUMN {col} {ddl}")
        self.db.execute("INSERT OR IGNORE INTO units SELECT chat_id, unit FROM subs WHERE unit IS NOT NULL")
        self.db.commit()

    # ----- raions -----
    def add_unit(self, chat_id: int, unit: str) -> bool:
        """False if the chat already follows MAX_UNITS raions."""
        self.db.execute("INSERT OR IGNORE INTO subs (chat_id) VALUES (?)", (chat_id,))
        have = self.units(chat_id)
        if unit in have:
            self.db.execute("UPDATE subs SET active=1 WHERE chat_id=?", (chat_id,))  # re-subscribe after /stop
            return True
        if len(have) >= MAX_UNITS:
            return False
        self.db.execute("INSERT INTO units VALUES (?, ?)", (chat_id, unit))
        self.db.execute("UPDATE subs SET unit=?, active=1 WHERE chat_id=?", (unit, chat_id))
        self.db.commit()
        return True

    def remove_unit(self, chat_id: int, unit: str) -> None:
        self.db.execute("DELETE FROM units WHERE chat_id=? AND unit=?", (chat_id, unit))
        left = self.units(chat_id)
        self.db.execute("UPDATE subs SET unit=? WHERE chat_id=?", (left[0] if left else None, chat_id))
        self.db.commit()

    def units(self, chat_id: int) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT unit FROM units WHERE chat_id=? ORDER BY rowid", (chat_id,))]

    def subscribe(self, chat_id: int, unit: str) -> None:  # kept for compatibility: one-raion subscribe
        self.add_unit(chat_id, unit)

    # ----- settings -----
    def set(self, chat_id: int, field: str, value) -> bool:
        """Only whitelisted fields and values; anything else is refused (e.g. tampered button data)."""
        if field not in ALLOWED or value not in ALLOWED[field]:
            return False
        self.db.execute(f"UPDATE subs SET {field}=? WHERE chat_id=?", (value, chat_id))  # field is whitelisted
        self.db.commit()
        return True

    def pause(self, chat_id: int, until: str | None) -> None:
        self.db.execute("UPDATE subs SET paused_until=? WHERE chat_id=?", (until, chat_id))
        self.db.commit()

    def get(self, chat_id: int) -> dict | None:
        r = self.db.execute("SELECT chat_id, unit, cp, morning, active, evening, style, banner, paused_until "
                            "FROM subs WHERE chat_id=?", (chat_id,)).fetchone()
        if not r:
            return None
        d = dict(zip(("chat_id", "unit", "cp", "morning", "active", "evening", "style", "banner", "paused_until"), r))
        d["units"] = self.units(chat_id)
        return d

    def active(self, day: str | None = None) -> list[dict]:
        """Active subscribers with at least one raion, not paused on `day` (YYYY-MM-DD)."""
        ids = [r[0] for r in self.db.execute("SELECT chat_id FROM subs WHERE active=1")]
        out = []
        for i in ids:
            s = self.get(i)
            if s["units"] and not (day and s["paused_until"] and day <= s["paused_until"]):
                out.append(s)
        return out

    # ----- idempotency and small state -----
    def mark_sent(self, chat_id: int, kind: str, day: str) -> bool:
        cur = self.db.execute("INSERT OR IGNORE INTO sent VALUES (?, ?, ?)", (chat_id, kind, day))
        return cur.rowcount == 1

    def unmark_sent(self, chat_id: int, kind: str, day: str) -> None:
        """A delivery failed for a transient reason: allow a retry."""
        self.db.execute("DELETE FROM sent WHERE chat_id=? AND kind=? AND day=?", (chat_id, kind, day))

    def kv(self, key: str, value: str | None = None) -> str | None:
        if value is None:
            r = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            return r[0] if r else None
        self.db.execute("INSERT INTO state VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.db.commit()
        return value
