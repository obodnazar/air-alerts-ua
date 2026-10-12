// A D1-compatible fake on node:sqlite, and a fake Telegram that records calls.
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";

class Stmt {
  constructor(db, sql) { this.db = db; this.sql = sql; this.args = []; }
  bind(...a) { this.args = a.map((x) => (x === undefined ? null : x)); return this; }
  async all() { return { results: this.db.prepare(this.sql).all(...this.args) }; }
  async first() { return this.db.prepare(this.sql).get(...this.args) ?? null; }
  async run() { const r = this.db.prepare(this.sql).run(...this.args); return { meta: { changes: Number(r.changes) } }; }
}
export function fakeD1() {
  const db = new DatabaseSync(":memory:");
  db.exec(readFileSync(new URL("../schema.sql", import.meta.url), "utf8"));
  return { prepare: (sql) => new Stmt(db, sql), batch: async (stmts) => Promise.all(stmts.map((s) => s.run())), raw: db };
}
export class FakeTG {
  constructor({ blocked = [], broken = [] } = {}) { this.calls = []; this.blocked = new Set(blocked); this.broken = new Set(broken); }
  async send(chat, text, kb) {
    const { Blocked } = await import("../src/telegram.js");
    if (this.blocked.has(chat)) throw new Blocked("blocked");
    if (this.broken.has(chat)) throw new Error("network");
    this.calls.push(["send", chat, text, kb]);
  }
  async edit(chat, mid, text, kb) { this.calls.push(["edit", chat, text, kb]); }
  async answer(id, text) { this.calls.push(["answer", id, text ?? null]); }
  async react(chat) { this.calls.push(["react", chat]); }
}
