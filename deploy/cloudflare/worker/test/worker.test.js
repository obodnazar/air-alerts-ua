import { test } from "node:test";
import assert from "node:assert/strict";
import { fakeD1, FakeTG } from "./fakes.js";
import { Store } from "../src/store.js";
import { Router } from "../src/router.js";
import { catalog, search } from "../src/catalog.js";
import * as ui from "../src/ui.js";
import worker, { dueSlots, deliver, invalid } from "../src/index.js";
import { Telegram, RateLimited } from "../src/telegram.js";
import { readFileSync } from "node:fs";

const T = (iso) => new Date(iso);
const msg = (text, chat = 1, id = 9) => ({ message: { chat: { id: chat }, message_id: id, text } });
const cb = (data, chat = 1) => ({ callback_query: { id: "q", data, message: { chat: { id: chat }, message_id: 5 } } });
const buttons = (kb) => kb.inline_keyboard.flat();
// a clock that moves 2 s per action, like a person tapping; pass step=0 to simulate spam
const setup = (step = 2000) => {
  const store = new Store(fakeD1()), tg = new FakeTG();
  let t = T("2026-10-12T14:00:00Z").getTime();
  return { store, tg, r: new Router(store, catalog, tg, () => new Date((t += step))) };
};
const uIdx = (name) => `u:${catalog.units.indexOf(name)}`;

test("schedule: DST-aware Kyiv time, weekends skipped, catch-up window", () => {
  assert.deepEqual(dueSlots(T("2026-10-12T17:05:00Z")).map((s) => s.slot), ["20:00"]);            // 20:05 EEST (UTC+3)
  assert.deepEqual(dueSlots(T("2026-10-26T18:05:00Z")).map((s) => s.slot), ["20:00"]);            // 20:05 EET after 25 Oct (UTC+2)
  assert.deepEqual(dueSlots(T("2026-10-12T17:40:00Z")).map((s) => s.slot), ["20:00", "20:30"]);
  assert.deepEqual(dueSlots(T("2026-10-16T17:05:00Z")), []);                                       // Friday evening -> Saturday
  assert.deepEqual(dueSlots(T("2026-10-12T05:10:00Z")).map((s) => [s.kind, s.day]), [["morning", "2026-10-12"]]);
  assert.deepEqual(dueSlots(T("2026-10-12T09:00:00Z")), []);
});

test("onboarding in three taps, then every settings screen", async () => {
  const { r, tg, store } = setup();
  await r.handle(msg("/start"));
  await r.handle(cb(`ob:${Object.keys(catalog.oblasts).indexOf("м. Київ")}`));
  await r.handle(cb(uIdx("Київ")));
  assert.match(tg.calls.filter((c) => c[0] === "edit").at(-1)[2], /Готово/);
  for (const s of ["root", "cp", "ev", "st", "m", "bn", "pause", "units"]) await r.handle(cb(`s:${s}`));
  await r.handle(cb("cp:11"));
  assert.equal((await store.get(1)).cp, 11);
  assert.deepEqual(tg.calls.at(-1), ["answer", "q", "Збережено ✓"]);
});

test("forged callbacks change nothing and always get an answer", async () => {
  const { r, tg, store } = setup();
  await r.handle(cb(uIdx("Київ")));
  const before = JSON.stringify(await store.get(1));
  for (const d of ["cp:99", "cp:²", "u:99999", "u:-1", "ob:abc", "ob:999", "ev:03:00", "st:evil", "m:'; DROP TABLE subs;--",
    "bn:7", "p:365", "rm:abc", "xx:1", ""]) {
    await r.handle(cb(d));
    assert.equal(tg.calls.at(-1)[0], "answer");
  }
  assert.equal(JSON.stringify(await store.get(1)), before);
});

test("town search: largest cities, occupied town, speed under the CPU budget", async () => {
  const cases = { "Біла Церква": "Білоцерківський район", "Буча": "Бучанський район", "Чернігів": "Чернігівський район",
    "Кременчук": "Кременчуцький район", "Новомосковськ": "Самарівський район", "Київ": "Київ", "Одеса": "Одеський район",
    "Кривий Ріг": "Криворізький район", "Ірпінь": "Бучанський район", "Львів": "Львівський район" };
  for (const [q, want] of Object.entries(cases)) assert.equal(search(q)[0][1], want, q);
  const { r, tg } = setup();
  await r.handle(msg("Мелітополь"));
  assert.match(tg.calls.at(-1)[2], /не обслуговується/);
  assert.ok(tg.calls.some((c) => c[0] === "react"));
  const t0 = performance.now();
  for (let i = 0; i < 20; i++) search("Кременчук");
  const ms = (performance.now() - t0) / 20;
  console.log(`search: ${ms.toFixed(2)} ms per query`);
  assert.ok(ms < 8, `search too slow for the 10 ms CPU budget: ${ms} ms`);
});

test("up to three raions; re-subscribe after /stop", async () => {
  const { r, store } = setup();
  for (const u of ["Київ", "Бучанський район", "Броварський район", "Обухівський район"]) await r.handle(cb(uIdx(u)));
  assert.deepEqual((await store.get(1)).units, ["Київ", "Бучанський район", "Броварський район"]);
  await r.handle(msg("/stop"));
  await r.handle(cb(uIdx("Київ")));
  assert.equal((await store.get(1)).active, 1);
});

test("rate limit per chat", async () => {
  const { r, tg } = setup(0);
  for (let i = 0; i < 20; i++) await r.handle(msg("/start"));
  assert.equal(tg.calls.filter((c) => c[0] === "send").length, 8);
});

const rows = ["Київ", "Бучанський район", "Харківський район"].map((unit) => ({ unit, p_alert_08: 0.4, usual: 0.3,
  p_clear_9: 0.2, p_clear_10: 0.3, p_clear_11: 0.4, p_clear_12: 0.5 }));

const EV = { kind: "evening", day: "2026-10-13", slot: "20:00" };
const fill = async (store, n, slot = "20:00", from = 1) => {
  for (let c = from; c < from + n; c++) { await store.addUnit(c, "Київ"); if (slot !== "20:00") await store.set(c, "evening", slot); }
};

test("delivery: slots, idempotent, blocked and transient failures", async () => {
  const store = new Store(fakeD1()), tg = new FakeTG({ blocked: [3], broken: [4] });
  for (const [c, u] of [[1, "Київ"], [2, "Бучанський район"], [3, "Харківський район"], [4, "Київ"], [5, "Київ"]]) await store.addUnit(c, u);
  await store.set(2, "cp", 12);
  await store.set(5, "evening", "21:00");
  assert.deepEqual(await deliver(store, tg, EV), { sent: 0, waiting: true }); // forecast not in yet
  await store.saveForecast("evening", "2026-10-13", { rows, banner: "" });
  assert.equal((await deliver(store, tg, EV)).sent, 2);
  assert.equal((await store.get(3)).active, 0);                                 // blocked
  assert.equal(await store.pending("evening", "2026-10-13", "20:00"), 1);       // chat 4 failed transiently
  assert.match(tg.calls.find((c) => c[1] === 2)[2], /до 12:00: 50%/);
  tg.broken.clear();
  assert.equal((await deliver(store, tg, EV)).sent, 1);
  assert.equal((await deliver(store, tg, EV)).sent, 0);                         // never twice
  assert.equal((await deliver(store, tg, { ...EV, slot: "21:00" })).sent, 1);
  assert.equal(tg.calls.filter((c) => c[0] === "send").length, 4);
});

test("one run: shared Telegram budget across slots, and under 50 D1 queries", async () => {
  const db = fakeD1(), store = new Store(db), tg = new FakeTG();
  await fill(store, 40, "20:00", 1); await fill(store, 40, "20:30", 101); await fill(store, 40, "21:00", 201);
  await store.saveForecast("evening", "2026-10-13", { rows, banner: "" });
  const run = async () => {
    db.queries = 0;
    const budget = { calls: 40 };
    let sent = 0;
    for (const s of dueSlots(T("2026-10-13T18:05:00Z")).map((x) => ({ ...x, day: "2026-10-13" }))) sent += (await deliver(store, tg, s, budget)).sent;
    assert.ok(db.queries <= 30, `D1 queries per run: ${db.queries}`);
    return sent;
  };
  assert.deepEqual(dueSlots(T("2026-10-13T18:05:00Z")).map((s) => s.slot), ["20:00", "20:30", "21:00"]); // 21:05 Kyiv
  assert.deepEqual([await run(), await run(), await run(), await run()], [40, 40, 40, 0]);
  assert.equal(new Set(tg.calls.map((c) => c[1])).size, 120);
});

test("a 429 stops the run; the rest stays pending", async () => {
  const store = new Store(fakeD1()), tg = new FakeTG({ limited: [3] });
  await fill(store, 10);
  await store.saveForecast("evening", "2026-10-13", { rows, banner: "" });
  const budget = { calls: 40 };
  assert.equal((await deliver(store, tg, EV, budget)).sent, 2);
  assert.ok(budget.stop);
  assert.equal(await store.pending("evening", "2026-10-13", "20:00"), 8);
  tg.limited.clear();
  assert.equal((await deliver(store, tg, EV)).sent, 8);
});

test("overlapping runs: a failure in run A is not lost when run B finishes first", async () => {
  const store = new Store(fakeD1());
  await fill(store, 1);
  await store.saveForecast("evening", "2026-10-13", { rows, banner: "" });
  let inner = null;
  const a = new FakeTG({ broken: [1], onSend: async () => { inner = await deliver(store, new FakeTG(), EV); } }); // B runs while A waits on Telegram
  assert.equal((await deliver(store, a, EV)).sent, 0);
  assert.equal(inner.sent, 0);                                                  // B saw the row claimed and left it
  assert.equal(await store.pending("evening", "2026-10-13", "20:00"), 1);       // A's failure put it back
  const c = new FakeTG();
  assert.equal((await deliver(store, c, EV)).sent, 1);                          // run C delivers it
});

test("a run that died: its claim expires after the lease", async () => {
  const store = new Store(fakeD1());
  await fill(store, 1);
  await store.saveForecast("evening", "2026-10-13", { rows, banner: "" });
  const t0 = Date.parse("2026-10-13T17:00:00Z");
  await store.plan("evening", "2026-10-13", "20:00");
  assert.equal((await store.claim("evening", "2026-10-13", "20:00", "dead-run", t0, 10)).length, 1); // claimed, never finished
  assert.equal((await deliver(store, new FakeTG(), EV, undefined, t0 + 60000)).sent, 0);
  assert.equal((await deliver(store, new FakeTG(), EV, undefined, t0 + 16 * 60000)).sent, 1);
});

test("parallel taps: per-chat and global limits, and the three-raion cap, hold", async () => {
  const store = new Store(fakeD1()), now = Date.now();
  const one = await Promise.all(Array.from({ length: 20 }, () => store.allowed(1, now)));
  assert.equal(one.filter(Boolean).length, 8);
  const many = await Promise.all(Array.from({ length: 100 }, (_, i) => store.allowed(1000 + i, now + 20000)));
  assert.equal(many.filter(Boolean).length, 25);
  await store.addUnit(7, "Київ"); await store.addUnit(7, "Бучанський район");
  const added = await Promise.all(["Броварський район", "Обухівський район"].map((u) => store.addUnit(7, u)));
  assert.deepEqual(added.sort(), [false, true]);
  assert.equal((await store.get(7)).units.length, 3);
  assert.equal(await store.addUnit(7, "Київ"), true);                          // already there: still fine
});

test("webhook and ingest require their secrets; ingest validates input", async () => {
  const env = { DB: fakeD1(), TELEGRAM_BOT_TOKEN: "x", WEBHOOK_SECRET: "w".repeat(32), INGEST_SECRET: "i".repeat(32) };
  const post = (path, body, headers = {}) => worker.fetch(new Request(`https://x${path}`, { method: "POST", body: JSON.stringify(body), headers }), env);
  assert.equal((await post("/telegram", msg("/start"))).status, 403);
  assert.equal((await post("/ingest", { kind: "evening", day: "2026-10-13", rows: [] })).status, 403);
  const auth = { Authorization: `Bearer ${env.INGEST_SECRET}` };
  assert.equal((await post("/ingest", { kind: "evil", day: "x", rows: [] }, auth)).status, 400);
  assert.equal((await post("/ingest", { kind: "evening", day: "2026-10-13", rows }, auth)).status, 200);
  assert.equal((await new Store(env.DB).forecast("evening", "2026-10-13")).rows.length, 3);
  assert.equal((await post("/ingest", { kind: "evening", day: "2026-10-13", rows: rows.slice(0, 1) }, auth)).status, 409); // first wins
  assert.equal((await post("/ingest", { kind: "evening", day: "2026-10-13", rows: rows.slice(0, 1), replace: true }, auth)).status, 200);
  assert.equal((await post("/ingest", { kind: "evening", day: "2026-10-14", rows }, { Authorization: env.INGEST_SECRET })).status, 403); // no "Bearer"
  assert.equal((await post("/setup", {})).status, 403);                    // setup also needs the secret
});

test("labels fit a phone; no red buttons", () => {
  const s = { units: ["Білоцерківський район"], cp: 10, evening: "20:00", style: "balanced", morning: "alert", banner: 1 };
  const index = Object.fromEntries(catalog.units.map((u, i) => [u, i]));
  const boards = [ui.oblastKeyboard(Object.keys(catalog.oblasts)), ui.settingsKeyboard(s),
    ...Object.keys(ui.SCREENS).map((n) => ui.screen(n, s)[1]), ...Object.values(catalog.oblasts).map((us) => ui.raionKeyboard(us, index))];
  for (const b of boards) for (const row of b.inline_keyboard) {
    for (const x of row) { assert.ok(x.text.length <= 30, x.text); assert.ok(!x.style || ["primary", "success"].includes(x.style)); }
    if (row.length === 2) assert.ok(row.every((x) => x.text.length <= 16), row.map((x) => x.text));
  }
});

test("transport retries 429 and never puts the token into an error", async () => {
  let n = 0;
  const fakeFetch = async () => (++n === 1 ? new Response(JSON.stringify({ parameters: { retry_after: 0 } }), { status: 429 })
    : new Response(JSON.stringify({ ok: true, result: { message_id: 1 } })));
  assert.deepEqual(await new Telegram("SECRET123", fakeFetch).send(1, "hi"), { message_id: 1 });
  const bad = async () => new Response("{}", { status: 500 });
  await assert.rejects(new Telegram("SECRET123", bad).send(1, "hi"), (e) => !String(e.message).includes("SECRET123"));
});

test("default fetch is called unbound (Workers throw 'Illegal invocation' otherwise)", async () => {
  const real = globalThis.fetch;
  globalThis.fetch = function (...a) {
    if (this !== undefined && this !== globalThis) throw new TypeError("Illegal invocation");
    return Promise.resolve(new Response(JSON.stringify({ ok: true, result: { ok: 1 } })));
  };
  try { assert.deepEqual(await new Telegram("t").call("getMe", {}), { ok: 1 }); } finally { globalThis.fetch = real; }
});

test("ingest: every bad payload from the audit is refused; real forecasts pass", () => {
  const ok = { kind: "evening", day: "2026-10-13", rows };
  const bad = [null, { ...ok, day: "2026-99-99" }, { ...ok, day: "2026-02-30" }, { ...ok, rows: [null] }, { ...ok, rows: [] },
    { ...ok, rows: [{ ...rows[0], unit: "unknown" }] }, { ...ok, rows: [{ ...rows[0], p_alert_08: 2 }] },
    { ...ok, rows: [{ ...rows[0], p_clear_10: -1 }] }, { ...ok, rows: [{ ...rows[0], p_clear_9: null }] },
    { ...ok, rows: [rows[0], rows[0]] }, { ...ok, rows: [{ ...rows[0], usual: "0.3" }] }, { ...ok, banner: {} },
    { kind: "morning", day: "2026-10-13", rows: [{ unit: "Київ", level: "green", elapsed_min: 5, p_clear_9: 0.1, p_clear_10: 0.1, p_clear_11: 0.1, p_clear_12: 0.1 }] }];
  for (const b of bad) assert.ok(invalid(b), JSON.stringify(b)?.slice(0, 80));
  assert.equal(invalid(ok), null);
  assert.equal(invalid({ kind: "morning", day: "2026-10-13", rows: [] }), null); // a quiet morning
  const csv = (f) => {
    const [head, ...lines] = readFileSync(new URL(`../../../../results/live/${f}`, import.meta.url), "utf8").trim().split("\n");
    const cols = head.split(",");
    return lines.map((l) => Object.fromEntries(l.split(",").map((v, i) => [cols[i], cols[i] === "unit" || cols[i] === "level" ? v : Number(v)])));
  };
  assert.equal(invalid({ kind: "evening", day: "2026-10-13", rows: csv("bot_evening_2026-10-13.csv") }), null);
  assert.equal(invalid({ kind: "morning", day: "2026-10-12", rows: csv("bot_morning_2026-10-12.csv") }), null);
});

test("transport: long 429 is not slept through; errors never carry the token", async () => {
  const slow = async () => new Response(JSON.stringify({ parameters: { retry_after: 30 } }), { status: 429 });
  const t0 = Date.now();
  await assert.rejects(new Telegram("SECRET123", slow).send(1, "hi"), RateLimited);
  assert.ok(Date.now() - t0 < 1000);
  const down = async (url) => { throw new TypeError(`fetch failed: ${url}`); };
  await assert.rejects(new Telegram("SECRET123", down).send(1, "hi"), (e) => !String(e.message).includes("SECRET123"));
  const env = { DB: fakeD1(), TELEGRAM_BOT_TOKEN: "SECRET123", WEBHOOK_SECRET: "w", INGEST_SECRET: "i".repeat(32) };
  const real = globalThis.fetch;
  globalThis.fetch = async (url) => { throw new TypeError(`fetch failed: ${url}`); };
  try {
    const res = await worker.fetch(new Request("https://x/setup", { method: "POST", headers: { Authorization: `Bearer ${env.INGEST_SECRET}` } }), env);
    assert.equal(res.status, 502);
    assert.ok(!(await res.text()).includes("SECRET123"));
  } finally { globalThis.fetch = real; }
});

test("parity with Python: half rounds to even, characters not UTF-16 units, whole-word normalisation", async () => {
  assert.equal(ui.bar(0.25), "▰▰▱▱▱▱▱▱▱▱");                                    // Python round(2.5) == 2
  assert.match(ui.eveningMessage({ units: ["Київ"], cp: 10, style: "balanced", banner: 0 },
    { Київ: { ...rows[0], p_alert_08: 0.125 } }, "2026-10-13"), /тривога о 08:00: <b>12%<\/b>/);
  const { norm } = await import("../src/catalog.js");
  assert.equal(norm("Красносільська громада"), "красносільська");
  assert.equal(norm("м. Київ"), "мкиїв");                                      // as Python: "м." is cut only before a letter
  const { r, tg } = setup();
  await r.handle(msg("😀".repeat(100) + " Київ"));
  assert.ok(tg.calls.some((c) => c[0] === "send" && /Київ/.test(c[2] || "")));
});

test("first contact explains who we are; a returning user gets shortcuts; help has buttons", async () => {
  const { r, tg } = setup();
  await r.handle(msg("/start"));
  let last = tg.calls.at(-1);
  assert.match(last[2], /Хто ми/);
  assert.match(last[2], /не сигнал безпеки/);
  assert.deepEqual(buttons(last[3]).map((b) => b.callback_data), ["back", "how"]);
  await r.handle(cb("how"));
  assert.match(tg.calls.filter((c) => c[0] === "send").at(-1)[2], /Як читати прогноз/);
  await r.handle(cb("back"));
  assert.equal(tg.calls.filter((c) => c[0] === "edit").at(-1)[2], "Оберіть область:");
  await r.handle(cb(uIdx("Київ")));
  await r.handle(msg("/start"));
  last = tg.calls.at(-1);
  assert.match(last[2], /З поверненням/);
  assert.deepEqual(buttons(last[3]).map((b) => b.callback_data), ["now", "s:root", "add", "help"]);
  await r.handle(msg("/help"));
  assert.match(tg.calls.at(-1)[2], /\/settings/);
  assert.ok(tg.calls.at(-1)[3]);
  for (const t of [ui.WELCOME, ui.HELP, ui.HOW]) assert.ok(t.length < 4096);
});

test("a forecast is delivered right after /ingest, not at the next tick", async () => {
  const env = { DB: fakeD1(), TELEGRAM_BOT_TOKEN: "x", WEBHOOK_SECRET: "w", INGEST_SECRET: "i".repeat(32) };
  const pending = [];
  const real = globalThis.fetch;
  const sent = [];
  globalThis.fetch = async (url, init) => { sent.push(JSON.parse(init.body).chat_id); return new Response(JSON.stringify({ ok: true, result: {} })); };
  const RealDate = Date;
  try {
    await new Store(env.DB).addUnit(1, "Київ");
    globalThis.Date = class extends RealDate { constructor(...a) { super(...(a.length ? a : ["2026-10-13T17:02:00Z"])); } static now() { return RealDate.parse("2026-10-13T17:02:00Z"); } };
    const res = await worker.fetch(new Request("https://x/ingest", { method: "POST", body: JSON.stringify({ kind: "evening", day: "2026-10-14", rows }),
      headers: { Authorization: `Bearer ${env.INGEST_SECRET}` } }), env, { waitUntil: (p) => pending.push(p) });
    assert.equal(res.status, 200);
    await Promise.all(pending);
    assert.deepEqual(sent, [1]);
  } finally { globalThis.fetch = real; globalThis.Date = RealDate; }
});
