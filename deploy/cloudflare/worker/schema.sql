-- Subscribers and their settings; only Telegram chat ids are stored.
CREATE TABLE IF NOT EXISTS subs (
  chat_id INTEGER PRIMARY KEY, unit TEXT, cp INTEGER DEFAULT 10, morning TEXT DEFAULT 'alert',
  active INTEGER DEFAULT 1, evening TEXT DEFAULT '20:00', style TEXT DEFAULT 'balanced',
  banner INTEGER DEFAULT 1, paused_until TEXT
);
CREATE TABLE IF NOT EXISTS units (chat_id INTEGER, unit TEXT, added INTEGER, PRIMARY KEY (chat_id, unit));
-- delivery outbox: one row per chat, kind and day (so never two messages). state: pending -> sent | skipped | blocked.
-- A run claims pending rows with its own token and a lease; rows of a run that died come back after the lease.
CREATE TABLE IF NOT EXISTS outbox (
  chat_id INTEGER, kind TEXT, day TEXT, slot TEXT, state TEXT DEFAULT 'pending', token TEXT, lease INTEGER DEFAULT 0,
  PRIMARY KEY (chat_id, kind, day)
);
CREATE INDEX IF NOT EXISTS outbox_due ON outbox (kind, day, slot, state);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
-- forecasts pushed by GitHub Actions to /ingest
CREATE TABLE IF NOT EXISTS forecasts (kind TEXT, day TEXT, body TEXT, received TEXT, PRIMARY KEY (kind, day));
-- rate limit window: per chat and global
CREATE TABLE IF NOT EXISTS hits (chat_id INTEGER, t INTEGER);
CREATE INDEX IF NOT EXISTS hits_chat ON hits (chat_id, t);
CREATE INDEX IF NOT EXISTS hits_t ON hits (t);
