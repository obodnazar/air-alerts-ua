-- 13 Oct 2026: applySeed compared every alert with every json_each row (N×M row reads, ~14 M per call) and used up
-- D1's free daily read limit at 08:01. Keyed staging table instead.
CREATE TABLE IF NOT EXISTS seed_keys (unit TEXT, started INTEGER, PRIMARY KEY (unit, started));
CREATE INDEX IF NOT EXISTS alerts_started ON alerts (started);
