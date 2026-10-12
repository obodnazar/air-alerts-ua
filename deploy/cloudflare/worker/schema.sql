-- Subscribers and their settings; only Telegram chat ids are stored.
CREATE TABLE IF NOT EXISTS subs (
  chat_id INTEGER PRIMARY KEY, unit TEXT, cp INTEGER DEFAULT 10, morning TEXT DEFAULT 'alert',
  active INTEGER DEFAULT 1, evening TEXT DEFAULT '20:00', style TEXT DEFAULT 'balanced',
  banner INTEGER DEFAULT 1, paused_until TEXT
);
CREATE TABLE IF NOT EXISTS units (chat_id INTEGER, unit TEXT, added INTEGER, PRIMARY KEY (chat_id, unit));
-- one message per chat, kind and day: idempotent, restart-safe delivery
CREATE TABLE IF NOT EXISTS sent (chat_id INTEGER, kind TEXT, day TEXT, PRIMARY KEY (chat_id, kind, day));
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
-- forecasts pushed by GitHub Actions to /ingest
CREATE TABLE IF NOT EXISTS forecasts (kind TEXT, day TEXT, body TEXT, received TEXT, PRIMARY KEY (kind, day));
-- per-chat rate limit window
CREATE TABLE IF NOT EXISTS hits (chat_id INTEGER, t INTEGER);
CREATE INDEX IF NOT EXISTS hits_chat ON hits (chat_id, t);
