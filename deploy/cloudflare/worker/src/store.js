// D1 storage, ported from src/airalerts/botapp/store.py (whitelisted fields, idempotent delivery log).
export const MAX_UNITS = 3;
export const ALLOWED = {
  cp: new Set([9, 10, 11, 12]), morning: new Set(["alert", "always"]), evening: new Set(["20:00", "20:30", "21:00"]),
  style: new Set(["careful", "balanced", "bold"]), banner: new Set([0, 1]), active: new Set([0, 1]),
};

export class Store {
  constructor(db) { this.db = db; }
  q(sql, ...args) { return this.db.prepare(sql).bind(...args); }

  async units(chat) {
    const r = await this.q("SELECT unit FROM units WHERE chat_id=? ORDER BY added, rowid", chat).all();
    return r.results.map((x) => x.unit);
  }
  async get(chat) {
    const s = await this.q("SELECT * FROM subs WHERE chat_id=?", chat).first();
    if (!s) return null;
    return { ...s, units: await this.units(chat) };
  }
  /** One transaction: the count check and the insert cannot interleave with a parallel tap (audit round 2). */
  async addUnit(chat, unit) {
    await this.db.batch([
      this.q("INSERT OR IGNORE INTO subs (chat_id) VALUES (?)", chat),
      this.q(`INSERT OR IGNORE INTO units SELECT ?1, ?2, ?3 WHERE (SELECT count(*) FROM units WHERE chat_id=?1) < ?4
              OR EXISTS (SELECT 1 FROM units WHERE chat_id=?1 AND unit=?2)`, chat, unit, Date.now(), MAX_UNITS),
      this.q("UPDATE subs SET unit=?2, active=1 WHERE chat_id=?1 AND EXISTS (SELECT 1 FROM units WHERE chat_id=?1 AND unit=?2)", chat, unit),
    ]);
    return !!(await this.q("SELECT 1 AS ok FROM units WHERE chat_id=? AND unit=?", chat, unit).first());
  }
  async removeUnit(chat, unit) {
    await this.q("DELETE FROM units WHERE chat_id=? AND unit=?", chat, unit).run();
    const left = await this.units(chat);
    await this.q("UPDATE subs SET unit=? WHERE chat_id=?", left[0] ?? null, chat).run();
  }
  /** Only whitelisted fields and values; anything else (e.g. forged button data) is refused. */
  async set(chat, field, value) {
    if (!(field in ALLOWED) || !ALLOWED[field].has(value)) return false;
    await this.q(`UPDATE subs SET ${field}=? WHERE chat_id=?`, value, chat).run(); // field is whitelisted
    return true;
  }
  async pause(chat, until) { await this.q("UPDATE subs SET paused_until=? WHERE chat_id=?", until, chat).run(); }

  // ---------- delivery outbox (see schema.sql); every step is one statement, so a run stays far under 50 D1 queries ----------
  /** Queue every subscriber due for this slot. Idempotent (one row per chat, kind and day), so it runs on every tick. */
  async plan(kind, day, slot) {
    await this.q(`INSERT OR IGNORE INTO outbox (chat_id, kind, day, slot)
      SELECT chat_id, ?1, ?2, ?3 FROM subs s WHERE active=1 AND (paused_until IS NULL OR paused_until < ?2)
        AND (?1 = 'morning' OR evening = ?3) AND EXISTS (SELECT 1 FROM units u WHERE u.chat_id = s.chat_id)`, kind, day, slot).run();
  }
  /** Claim up to `limit` pending rows with this run's token; a row claimed by a run that died is free again after its lease. */
  async claim(kind, day, slot, token, now, limit, leaseMs = 15 * 60000) {
    await this.q(`UPDATE outbox SET token=?1, lease=?2 WHERE rowid IN (SELECT rowid FROM outbox
      WHERE kind=?3 AND day=?4 AND slot=?5 AND state='pending' AND (token IS NULL OR lease < ?6)
        AND EXISTS (SELECT 1 FROM subs s WHERE s.chat_id = outbox.chat_id AND s.active = 1
          AND (s.paused_until IS NULL OR s.paused_until < ?4)) LIMIT ?7)`,
    token, now + leaseMs, kind, day, slot, now, limit).run();
    const subs = (await this.q("SELECT s.* FROM subs s JOIN outbox o ON o.chat_id=s.chat_id WHERE o.token=?", token).all()).results;
    const units = (await this.q(`SELECT u.chat_id, u.unit FROM units u JOIN outbox o ON o.chat_id=u.chat_id
      WHERE o.token=? ORDER BY u.added, u.rowid`, token).all()).results;
    return subs.map((s) => ({ ...s, units: units.filter((u) => u.chat_id === s.chat_id).map((u) => u.unit) }));
  }
  /** Close a run: {chat_id: "sent" | "skipped" | "blocked"}; any claimed row not in it goes back to pending.
   *  Only rows still carrying this run's token are touched, so a slow run cannot undo a newer one. */
  async finish(token, outcome) {
    const blocked = Object.keys(outcome).filter((c) => outcome[c] === "blocked").map(Number);
    await this.db.batch([
      this.q(`UPDATE outbox SET state=coalesce(json_extract(?1, '$."' || chat_id || '"'), 'pending'), token=NULL, lease=0
        WHERE token=?2`, JSON.stringify(outcome), token),
      ...(blocked.length ? [this.q("UPDATE subs SET active=0 WHERE chat_id IN (SELECT value FROM json_each(?))", JSON.stringify(blocked))] : []),
    ]);
  }
  async pending(kind, day, slot) {
    return (await this.q("SELECT count(*) AS n FROM outbox WHERE kind=? AND day=? AND slot=? AND state='pending'", kind, day, slot).first()).n;
  }
  async kv(key, value) {
    if (value === undefined) return (await this.q("SELECT value FROM state WHERE key=?", key).first())?.value ?? null;
    await this.q("INSERT INTO state VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", key, value).run();
    return value;
  }
  /** replace=false: first version wins, atomically (returns false if one was already there). */
  async saveForecast(kind, day, body, replace = true) {
    const r = await this.q(`INSERT INTO forecasts VALUES (?, ?, ?, ?) ON CONFLICT(kind, day) DO ${replace
      ? "UPDATE SET body=excluded.body, received=excluded.received" : "NOTHING"}`, kind, day, JSON.stringify(body), new Date().toISOString()).run();
    return r.meta.changes === 1;
  }
  async forecast(kind, day) {
    const r = await this.q("SELECT body FROM forecasts WHERE kind=? AND day=?", kind, day).first();
    return r ? JSON.parse(r.body) : null;
  }
  async saveHourly(day, body) {
    await this.db.batch([
      this.q("INSERT INTO forecasts VALUES ('hourly', ?1, ?2, ?3) ON CONFLICT(kind, day) DO UPDATE SET body=excluded.body, received=excluded.received",
        day, JSON.stringify(body), new Date().toISOString()),
      this.q("DELETE FROM forecasts WHERE kind='hourly' AND day < ?", day),
    ]);
  }
  async hourlyParams() {
    const r = await this.q("SELECT body FROM forecasts WHERE kind='hourly' ORDER BY day DESC LIMIT 1").first();
    return r ? JSON.parse(r.body) : null;
  }

  // ---------- live alerts (schema.sql: alerts); every call is one or two statements ----------
  /** {last: newest post id processed, ok: ms of the last good scrape, gapTo: posts up to this id may be missing (0 = none)} */
  async liveMeta() {
    const rows = (await this.q("SELECT key, value FROM state WHERE key IN ('live_last_id', 'live_ok', 'live_gap_to')").all()).results;
    const kv = Object.fromEntries(rows.map((r) => [r.key, Number(r.value) || 0]));
    return { last: kv.live_last_id || 0, ok: kv.live_ok || 0, gapTo: kv.live_gap_to || 0 };
  }
  async openAlerts(units = null) {
    const sql = "SELECT * FROM alerts WHERE ended IS NULL" + (units ? " AND unit IN (SELECT value FROM json_each(?))" : "") + " ORDER BY started";
    return (await (units ? this.q(sql, JSON.stringify(units)) : this.q(sql)).all()).results;
  }
  /** Write what a scrape changed and its bookkeeping in one batch, only if the cursor is still `prev`
   *  (compare-and-swap: a slower overlapping run cannot roll a newer state back). Returns whether it applied. */
  async saveLive(rows, meta, prev = null) {
    const cur = "(?2 IS NULL OR coalesce((SELECT value FROM state WHERE key='live_last_id'), '0') = ?2)";
    const res = await this.db.batch([
      ...(rows.length ? [this.q(`INSERT INTO alerts (unit, started, ended, level, max_level)
        SELECT json_extract(value, '$.unit'), json_extract(value, '$.started'), json_extract(value, '$.ended'),
          json_extract(value, '$.level'), json_extract(value, '$.max_level') FROM json_each(?1) WHERE ${cur}
        ON CONFLICT(unit, started) DO UPDATE SET ended=coalesce(excluded.ended, alerts.ended), level=excluded.level,
          max_level=excluded.max_level`, JSON.stringify(rows), prev == null ? null : String(prev))] : []),
      this.q(`INSERT INTO state (key, value) SELECT key, value FROM json_each(?1) WHERE ${cur}
        ON CONFLICT(key) DO UPDATE SET value=excluded.value`,
      JSON.stringify({ live_last_id: String(meta.last), live_ok: String(meta.ok), live_gap_to: String(meta.gapTo) }), prev == null ? null : String(prev)),
    ]);
    return res.at(-1).meta.changes > 0;
  }
  /** Alert history from the Python pipeline (kind "alerts"): authoritative for alerts that started in [since, dataEnd],
   *  which is how it heals posts the Worker missed. An all-clear seen by either side wins (it is a fact); of two
   *  open rows of one raion only the earliest is kept (a later "start" was a level change seen without its start). */
  async applySeed(seed, now = Date.now()) {
    const meta = await this.liveMeta();
    const newer = seed.max_post_id >= meta.last ? 1 : 0;
    const rows = JSON.stringify(seed.alerts);
    const rank = (x) => `CASE ${x} WHEN 'red' THEN 2 WHEN 'yellow' THEN 1 ELSE 0 END`;
    await this.db.batch([
      this.q(`DELETE FROM alerts WHERE started >= ?1 AND started <= ?2 AND NOT EXISTS (SELECT 1 FROM json_each(?3) j
        WHERE json_extract(j.value, '$[0]') = alerts.unit AND json_extract(j.value, '$[1]') = alerts.started)`, seed.since, seed.data_end, rows),
      this.q(`INSERT INTO alerts (unit, started, ended, level, max_level)
        SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]'), json_extract(value, '$[2]'), json_extract(value, '$[3]'),
          json_extract(value, '$[4]') FROM json_each(?1) WHERE true
        ON CONFLICT(unit, started) DO UPDATE SET ended=coalesce(alerts.ended, excluded.ended),
          level=CASE WHEN ?2 = 1 AND alerts.ended IS NULL THEN excluded.level ELSE alerts.level END,
          max_level=CASE WHEN ${rank("excluded.max_level")} > ${rank("alerts.max_level")} THEN excluded.max_level ELSE alerts.max_level END`,
      rows, newer),
      this.q(`DELETE FROM alerts WHERE ended IS NULL AND EXISTS (SELECT 1 FROM alerts b WHERE b.unit = alerts.unit
        AND b.ended IS NULL AND b.started < alerts.started)`),
      this.q("DELETE FROM alerts WHERE started < ?", now - 14 * 86400000),
      this.q(`INSERT INTO state (key, value) SELECT key, value FROM json_each(?) WHERE true
        ON CONFLICT(key) DO UPDATE SET value=excluded.value`, JSON.stringify({
        live_last_id: String(Math.max(meta.last, seed.max_post_id)),
        live_gap_to: String(meta.gapTo && seed.max_post_id >= meta.gapTo ? 0 : meta.gapTo) })),
    ]);
    return { rows: seed.alerts.length, newer: !!newer };
  }
  /** The last n alerts of each unit, and today's count and minutes under alert (since dayStart, Kyiv midnight). */
  async recent(units, dayStart, now, n = 5) {
    const u = JSON.stringify(units);
    const [last, today] = await Promise.all([
      this.q(`SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY unit ORDER BY started DESC) AS rn FROM alerts
        WHERE unit IN (SELECT value FROM json_each(?1))) WHERE rn <= ?2 ORDER BY unit, started DESC`, u, n).all(),
      this.q(`SELECT unit, count(*) AS n, sum(min(coalesce(ended, ?3), ?3) - max(started, ?2)) AS ms FROM alerts
        WHERE unit IN (SELECT value FROM json_each(?1)) AND coalesce(ended, ?3) > ?2 AND started <= ?3 GROUP BY unit`, u, dayStart, now).all(),
    ]);
    const t = Object.fromEntries(today.results.map((r) => [r.unit, r]));
    return Object.fromEntries(units.map((x) => [x, { last: last.results.filter((r) => r.unit === x),
      todayCount: t[x]?.n || 0, todayMs: t[x]?.ms || 0 }]));
  }

  /** Per-chat and global limits, checked and recorded in one statement (atomic in D1, unlike count-then-insert). */
  async allowed(chat, now = Date.now(), max = 8, windowMs = 10000, globalMax = 25, globalMs = 1000) {
    const [, ins] = await this.db.batch([
      this.q("DELETE FROM hits WHERE t < ?", now - windowMs),
      this.q(`INSERT INTO hits SELECT ?1, ?2 WHERE (SELECT count(*) FROM hits WHERE chat_id=?1 AND t > ?3) < ?4
        AND (SELECT count(*) FROM hits WHERE t > ?5) < ?6`, chat, now, now - windowMs, max, now - globalMs, globalMax),
    ]);
    return ins.meta.changes === 1;
  }
}
