// A D1-compatible fake on node:sqlite, and a fake Telegram that records calls.
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";

class Stmt {
  constructor(db, sql) { this.db = db; this.sql = sql; this.args = []; }
  bind(...a) { this.args = a.map((x) => (x === undefined ? null : x)); return this; }
  async all() { return { results: this.db.prepare(this.sql).all(...this.args) }; }
  async first() { return this.db.prepare(this.sql).get(...this.args) ?? null; }
  runSync() { const r = this.db.prepare(this.sql).run(...this.args); return { meta: { changes: Number(r.changes) } }; }
  async run() { return this.runSync(); }
}
/** `queries` counts statements, as D1 does for its 50-queries-per-invocation limit (a batch counts each one). */
export function fakeD1() {
  const db = new DatabaseSync(":memory:");
  db.exec(readFileSync(new URL("../schema.sql", import.meta.url), "utf8"));
  const d1 = { queries: 0, raw: db, limits: { chat: new Map(), all: [] }, // a fresh rate-limit window per test database
    prepare: (sql) => { d1.queries++; return new Stmt(db, sql); },
    batch: async (stmts) => { // one synchronous transaction, as D1 runs a batch
      db.exec("BEGIN"); try { const r = stmts.map((s) => s.runSync()); db.exec("COMMIT"); return r; } catch (e) { db.exec("ROLLBACK"); throw e; } } };
  return d1;
}
export class FakeTG {
  constructor({ blocked = [], broken = [], limited = [], onSend = null } = {}) {
    this.calls = []; this.blocked = new Set(blocked); this.broken = new Set(broken); this.limited = new Set(limited); this.onSend = onSend;
  }
  async send(chat, text, kb) {
    const { Blocked, RateLimited } = await import("../src/telegram.js");
    if (this.onSend) await this.onSend(chat);
    if (this.blocked.has(chat)) throw new Blocked("blocked");
    if (this.limited.has(chat)) throw new RateLimited("sendMessage", 30);
    if (this.broken.has(chat)) throw new Error("network");
    this.calls.push(["send", chat, text, kb]);
  }
  async edit(chat, mid, text, kb) { this.calls.push(["edit", chat, text, kb]); }
  async answer(id, text) { this.calls.push(["answer", id, text ?? null]); }
  async react(chat) { this.calls.push(["react", chat]); }
}
