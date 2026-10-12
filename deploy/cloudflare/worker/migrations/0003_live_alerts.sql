-- 12 Oct 2026: hourly risk and recent alerts need the live alert state per raion (see schema.sql).
CREATE TABLE IF NOT EXISTS alerts (unit TEXT, started INTEGER, ended INTEGER, level TEXT, max_level TEXT, PRIMARY KEY (unit, started));
CREATE INDEX IF NOT EXISTS alerts_open ON alerts (ended, unit);
