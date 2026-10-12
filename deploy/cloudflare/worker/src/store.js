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
      WHERE kind=?3 AND day=?4 AND slot=?5 AND state='pending' AND (token IS NULL OR lease < ?6) LIMIT ?7)`,
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
  async saveForecast(kind, day, body) {
    await this.q("INSERT INTO forecasts VALUES (?, ?, ?, ?) ON CONFLICT(kind, day) DO UPDATE SET body=excluded.body, received=excluded.received",
      kind, day, JSON.stringify(body), new Date().toISOString()).run();
  }
  async forecast(kind, day) {
    const r = await this.q("SELECT body FROM forecasts WHERE kind=? AND day=?", kind, day).first();
    return r ? JSON.parse(r.body) : null;
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
