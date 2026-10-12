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
  async addUnit(chat, unit) {
    await this.q("INSERT OR IGNORE INTO subs (chat_id) VALUES (?)", chat).run();
    const have = await this.units(chat);
    if (have.includes(unit)) { await this.q("UPDATE subs SET active=1 WHERE chat_id=?", chat).run(); return true; }
    if (have.length >= MAX_UNITS) return false;
    await this.db.batch([
      this.q("INSERT INTO units VALUES (?, ?, ?)", chat, unit, Date.now()),
      this.q("UPDATE subs SET unit=?, active=1 WHERE chat_id=?", unit, chat),
    ]);
    return true;
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

  async active(day) {
    const r = await this.q("SELECT chat_id FROM subs WHERE active=1 AND (paused_until IS NULL OR paused_until < ?)", day).all();
    const out = [];
    for (const { chat_id } of r.results) { const s = await this.get(chat_id); if (s.units.length) out.push(s); }
    return out;
  }
  async markSent(chat, kind, day) {
    const r = await this.q("INSERT OR IGNORE INTO sent VALUES (?, ?, ?)", chat, kind, day).run();
    return r.meta.changes === 1;
  }
  async unmarkSent(chat, kind, day) { await this.q("DELETE FROM sent WHERE chat_id=? AND kind=? AND day=?", chat, kind, day).run(); }
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
  /** Per-chat limit: at most `max` actions in `windowMs`. */
  async allowed(chat, now = Date.now(), max = 8, windowMs = 10000) {
    await this.q("DELETE FROM hits WHERE t < ?", now - windowMs).run();
    const n = (await this.q("SELECT count(*) AS n FROM hits WHERE chat_id=?", chat).first()).n;
    if (n >= max) return false;
    await this.q("INSERT INTO hits VALUES (?, ?)", chat, now).run();
    return true;
  }
}
