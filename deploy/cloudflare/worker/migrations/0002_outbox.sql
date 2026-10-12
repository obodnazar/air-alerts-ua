-- 12 Oct 2026 (Codex audit, round 2): the `sent` log marked a message before it was sent, so overlapping
-- runs could lose it. Replaced by an outbox with per-run claims and leases.
CREATE TABLE IF NOT EXISTS outbox (
  chat_id INTEGER, kind TEXT, day TEXT, slot TEXT, state TEXT DEFAULT 'pending', token TEXT, lease INTEGER DEFAULT 0,
  PRIMARY KEY (chat_id, kind, day)
);
CREATE INDEX IF NOT EXISTS outbox_due ON outbox (kind, day, slot, state);
INSERT OR IGNORE INTO outbox (chat_id, kind, day, slot, state) SELECT chat_id, kind, day, '', 'sent' FROM sent;
DROP TABLE IF EXISTS sent;
CREATE INDEX IF NOT EXISTS hits_t ON hits (t);
