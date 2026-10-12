import { test } from "node:test";
import assert from "node:assert/strict";
import { fakeD1, FakeTG } from "./fakes.js";
import { Store } from "../src/store.js";
import { Router } from "../src/router.js";
import { catalog, search } from "../src/catalog.js";
import * as ui from "../src/ui.js";
import worker, { dueSlots, deliver } from "../src/index.js";
import { Telegram } from "../src/telegram.js";

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

test("delivery: slots, batching, idempotent, blocked and transient failures", async () => {
  const store = new Store(fakeD1()), tg = new FakeTG({ blocked: [3], broken: [4] });
  for (const [c, u] of [[1, "Київ"], [2, "Бучанський район"], [3, "Харківський район"], [4, "Київ"], [5, "Київ"]]) await store.addUnit(c, u);
  await store.set(2, "cp", 12);
  await store.set(5, "evening", "21:00");
  const slot = { kind: "evening", day: "2026-10-13", slot: "20:00" };
  assert.deepEqual(await deliver(store, tg, slot), { sent: 0, done: false, waiting: true }); // forecast not in yet
  await store.saveForecast("evening", "2026-10-13", { rows, banner: "" });
  let res = await deliver(store, tg, slot);
  assert.equal(res.sent, 2);
  assert.equal(res.done, false);                         // chat 4 failed transiently
  assert.equal((await store.get(3)).active, 0);           // blocked
  assert.match(tg.calls.find((c) => c[1] === 2)[2], /до 12:00: 50%/);
  tg.broken.clear();
  res = await deliver(store, tg, slot);
  assert.deepEqual(res, { sent: 1, done: true });
  assert.deepEqual(await deliver(store, tg, slot), { sent: 0, done: true });
  assert.equal((await deliver(store, tg, { ...slot, slot: "21:00" })).sent, 1);
});

test("delivery batches at most 40 messages per invocation", async () => {
  const store = new Store(fakeD1()), tg = new FakeTG();
  for (let c = 1; c <= 95; c++) await store.addUnit(c, "Київ");
  await store.saveForecast("evening", "2026-10-13", { rows, banner: "" });
  const slot = { kind: "evening", day: "2026-10-13", slot: "20:00" };
  const sizes = [];
  for (let i = 0; i < 4; i++) sizes.push((await deliver(store, tg, slot)).sent);
  assert.deepEqual(sizes, [40, 40, 15, 0]);
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
