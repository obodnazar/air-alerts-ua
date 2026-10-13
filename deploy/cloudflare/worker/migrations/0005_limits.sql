-- 14 Oct 2026, Codex audit round 4 (free-tier limits): token and day indexes on the outbox; the rate limit moved to
-- memory (hits cost ~6 writes per tap); the seed no longer stages keys (2N writes per call).
CREATE INDEX IF NOT EXISTS outbox_token ON outbox (token);
CREATE INDEX IF NOT EXISTS outbox_day ON outbox (day);
DROP TABLE IF EXISTS hits;
DROP TABLE IF EXISTS seed_keys;
DELETE FROM outbox WHERE day < '2026-10-11';
