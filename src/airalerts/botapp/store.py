"""SQLite: subscribers, their settings, and a send log so a message is never sent twice for the same day."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path("data/bot/bot.sqlite")


class Store:
    def __init__(self, path: Path | str = DB):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS subs (chat_id INTEGER PRIMARY KEY, unit TEXT, cp INTEGER DEFAULT 10,
                morning TEXT DEFAULT 'alert', active INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS sent (chat_id INTEGER, kind TEXT, day TEXT, PRIMARY KEY (chat_id, kind, day));
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
        """)

    def subscribe(self, chat_id: int, unit: str) -> None:
        self.db.execute("INSERT INTO subs (chat_id, unit) VALUES (?, ?) ON CONFLICT(chat_id) DO UPDATE SET unit=excluded.unit, active=1",
                        (chat_id, unit))
        self.db.commit()

    def set(self, chat_id: int, field: str, value) -> None:
        assert field in ("cp", "morning", "active")
        self.db.execute(f"UPDATE subs SET {field}=? WHERE chat_id=?", (value, chat_id))
        self.db.commit()

    def get(self, chat_id: int) -> dict | None:
        r = self.db.execute("SELECT chat_id, unit, cp, morning, active FROM subs WHERE chat_id=?", (chat_id,)).fetchone()
        return dict(zip(("chat_id", "unit", "cp", "morning", "active"), r)) if r else None

    def active(self) -> list[dict]:
        rows = self.db.execute("SELECT chat_id, unit, cp, morning FROM subs WHERE active=1 AND unit IS NOT NULL").fetchall()
        return [dict(zip(("chat_id", "unit", "cp", "morning"), r)) for r in rows]

    def mark_sent(self, chat_id: int, kind: str, day: str) -> bool:
        """True if this is the first time; False if it was already sent (idempotent jobs)."""
        try:
            self.db.execute("INSERT INTO sent VALUES (?, ?, ?)", (chat_id, kind, day))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def kv(self, key: str, value: str | None = None) -> str | None:
        if value is None:
            r = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            return r[0] if r else None
        self.db.execute("INSERT INTO state VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.db.commit()
        return value
