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
  assert.deepEqual(buttons(last[3]).map((b) => b.callback_data), ["now", "hr", "rc", "s:root", "add", "help"]);
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

// ---------- 🕐 hourly risk, 📜 recent alerts, live scrape (12 Oct, after submission) ----------
import { parsePage, toEvents, applyEvents, unitType, unescapeHtml } from "../src/signal.js";
import { kyivOffset, kyivHour, kyivToUtc, coverageShare, forecastOne, bucketOf, hourRisk } from "../src/hourly.js";
import { scrape, liveState, PAGES } from "../src/live.js";
import { invalidHourly, invalidAlerts } from "../src/index.js";

const fixture = (f) => JSON.parse(readFileSync(new URL(`./fixtures/${f}`, import.meta.url), "utf8"));
const SIG = fixture("signal_parity.json"), HOURLY = fixture("hourly_parity.json");
const PAGE = readFileSync(new URL("./fixtures/tme_page.html", import.meta.url), "utf8");
const SIG_POSTS = SIG.posts.map((p) => ({ ...p, published_at: Date.parse(p.published_at) }));
const MINUTE = 60000;

/** A t.me/s page in the real markup's shape (text escaped the way t.me escapes it). */
const tmePage = (posts) => posts.map((p) => `<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="UkraineAlarmSignal/${p.id}">` +
  `<div class="tgme_widget_message_text js-message_text" dir="auto">${p.text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/'/g, "&#39;").replace(/!/g, "&#33;").replace(/\n/g, "<br/>")}</div>` +
  `<span class="tgme_widget_message_meta"><time datetime="${new Date(p.published_at).toISOString().replace(".000Z", "+00:00")}" class="time">x</time></span></div></div>`).join("\n");
/** A fake t.me: the live page shows the newest 20 posts up to `head`; ?before=X the 20 before X. */
function fakeTme(posts) {
  const fake = { head: 0, calls: 0 };
  fake.fetch = async (url) => {
    fake.calls++;
    const before = Number(new URL(url).searchParams.get("before")) || Infinity;
    const visible = posts.filter((p) => p.id <= fake.head && p.id < before).slice(-20);
    return new Response(tmePage(visible));
  };
  return fake;
}

test("signal: the real t.me page parses exactly as Python's scraper does", () => {
  const got = parsePage(PAGE), want = fixture("tme_page.json");
  assert.equal(got.length, 20);
  assert.deepEqual(got.map((p) => [p.id, p.published_at, p.text]), want.map((p) => [p.id, Date.parse(p.published_at), p.text]));
  assert.equal(unescapeHtml("&#33;&#39;&amp;&lt;&nbsp;&bogus;"), "!'&< &bogus;");
});

test("signal: 600 real posts give Python's events and Python's raion episodes", () => {
  const ev = toEvents(SIG_POSTS);
  assert.deepEqual(ev.map((e) => [e.post, e.unit, e.oblast, e.kind, e.level]), SIG.events.filter((e) => e[3] !== "threat")); // threats pair nothing
  const open = {}, rows = applyEvents(open, ev, (u) => ["raion", "kyiv"].includes(unitType(u)));
  const key = (r) => JSON.stringify(r);
  const got = rows.map((r) => [r.unit, r.started, r.ended, r.max_level, r.level]).map(key).sort();
  assert.deepEqual(got, SIG.episodes.map(key).sort());
});

test("signal: CPU per scrape stays far below the 10 ms Free limit", () => {
  const t0 = performance.now();
  for (let i = 0; i < 50; i++) {
    const posts = parsePage(PAGE);
    applyEvents({}, toEvents(posts), () => true);
  }
  const ms = (performance.now() - t0) / 50;
  console.log(`scrape: ${ms.toFixed(2)} ms CPU to parse and apply one real page (${(PAGE.length / 1024).toFixed(0)} KB)`);
  assert.ok(ms < 4, `parse + apply too slow: ${ms} ms`);
});

test("hourly: JS forecasts equal Python's on 291 cases, incl. the 25 Oct DST switch", () => {
  const { params, cases } = HOURLY;
  assert.equal(invalidHourly(params), null);
  for (const c of cases) {
    assert.equal(kyivToUtc(c.day, c.hour), c.target, `${c.day} ${c.hour}`);
    assert.equal(kyivHour(c.target), c.local_hour);
    assert.equal(Math.floor((c.target - c.now) / (10 * MINUTE)), c.steps);
    assert.equal(bucketOf(params.gates.buckets, (c.target - c.now) / 3600000), c.bucket);
    const u = params.units[c.unit], cs = coverageShare(u.prof, params.surv, c.target, 10);
    const p = forecastOne(u.clim[c.local_hour], cs, params.surv, c.on, c.elapsed, Math.min(c.steps, 288));
    assert.ok(Math.abs(p - c.p) < 1e-9, `${JSON.stringify(c)}: ${p}`);
  }
});

test("hourly: the Kyiv offset rule equals Intl for every hour of 2026 and 2027", () => {
  const fmt = new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/Kyiv", hour: "2-digit", hourCycle: "h23" });
  for (let t = Date.UTC(2026, 0, 1); t < Date.UTC(2028, 0, 1); t += 3600000) assert.equal(kyivHour(t), Number(fmt.format(t)), new Date(t).toISOString());
  assert.equal(kyivOffset(Date.UTC(2026, 9, 25, 0, 59)), 180);
  assert.equal(kyivOffset(Date.UTC(2026, 9, 25, 1, 0)), 120);
});

test("hourly: the model is shown only where the backtest earned it; no live data -> history only", () => {
  const { params } = HOURLY, now = Date.parse("2026-10-12T11:20:00Z"); // 14:20 Kyiv
  const on = hourRisk(params, "Київ", "2026-10-12", 15, now, { on: true, elapsed: 30 });
  assert.ok(on.model > 0 && on.bucket === 0);                                     // alert on, 1 h: model
  assert.equal(hourRisk(params, "Київ", "2026-10-12", 15, now, { on: false, elapsed: 0 }).model, null); // no alert: history
  assert.equal(hourRisk(params, "Київ", "2026-10-13", 22, now, { on: true, elapsed: 30 }).model, null); // 32 h ahead: history
  assert.equal(hourRisk(params, "Київ", "2026-10-12", 15, now, null).model, null);     // stale live data
  assert.ok(hourRisk(params, "Київ", "2026-10-12", 14, now, null).past);
  assert.equal(hourRisk(params, "Неіснуючий район", "2026-10-12", 15, now, null), null);
});

test("live: 5-minute ticks over 600 real posts end with Python's episodes for the served raions", async () => {
  const store = new Store(fakeD1()), tme = fakeTme(SIG_POSTS), units = catalog.units;
  await store.saveLive([], { last: SIG_POSTS[0].id - 1, ok: 1, gapTo: 0 });
  let t = SIG_POSTS[0].published_at, i = 0;
  while (i < SIG_POSTS.length) {
    t += 5 * MINUTE;
    while (i < SIG_POSTS.length && SIG_POSTS[i].published_at <= t) i++;
    if (!i) continue;
    tme.head = SIG_POSTS[i - 1].id;
    const r = await scrape(store, tme.fetch, units, t);
    assert.equal(r.gap, false);
  }
  const want = SIG.episodes.filter((e) => units.includes(e[0])).map((e) => JSON.stringify(e.slice(0, 4))).sort();
  const rows = store.db.raw.prepare("SELECT unit, started, ended, max_level FROM alerts").all();
  assert.deepEqual(rows.map((r) => JSON.stringify([r.unit, r.started, r.ended, r.max_level])).sort(), want);
});

test("live: one tick uses at most 4 D1 queries and PAGES fetches; a burst beyond the budget is flagged as a gap", async () => {
  const db = fakeD1(), store = new Store(db), tme = fakeTme(SIG_POSTS);
  await store.saveLive([], { last: SIG_POSTS[0].id - 1, ok: 1, gapTo: 0 });
  tme.head = SIG_POSTS[30].id;                                     // 31 new posts: two pages
  db.queries = 0; tme.calls = 0;
  const r = await scrape(store, tme.fetch, catalog.units, 1e12);
  assert.equal(r.posts, 31); assert.equal(r.gap, false);
  assert.ok(db.queries <= 4, `D1 queries: ${db.queries}`);
  assert.equal(tme.calls, 2);
  tme.head = SIG_POSTS[30 + 20 * PAGES + 5].id;                    // more new posts than PAGES pages hold
  tme.calls = 0;
  const g = await scrape(store, tme.fetch, catalog.units, 1e12 + 5 * MINUTE);
  assert.equal(tme.calls, PAGES); assert.equal(g.gap, true);
  const meta = await store.liveMeta();
  assert.equal(meta.last, tme.head);
  assert.equal(liveState(meta, null, 1e12 + 6 * MINUTE), null);    // unknown "now" until the gap is healed
});

test("live: first run knows nothing before its page; an empty or blocked page renews nothing", async () => {
  const store = new Store(fakeD1()), tme = fakeTme(SIG_POSTS);
  tme.head = SIG_POSTS[100].id;
  assert.equal((await scrape(store, tme.fetch, catalog.units, 1e12)).gap, true);
  const ok = (await store.liveMeta()).ok;
  await assert.rejects(scrape(store, async () => new Response("<html>captcha</html>"), catalog.units, 1e12 + MINUTE));
  await assert.rejects(scrape(store, async () => new Response("", { status: 429 }), catalog.units, 1e12 + MINUTE));
  assert.equal((await store.liveMeta()).ok, ok);
  assert.equal(liveState({ ok, gapTo: 0 }, null, ok + 16 * MINUTE), null); // stale after 15 min
  assert.deepEqual(liveState({ ok, gapTo: 0 }, null, ok + 5 * MINUTE), { on: false, elapsed: 0, level: null });
});

test("seed from /ingest: heals missed posts, an all-clear wins, artifacts go, the gap closes", async () => {
  const store = new Store(fakeD1()), H = 3600000, t0 = Date.parse("2026-10-12T06:00:00Z");
  // the Worker's own view: it missed the start of Kyiv's alert and saw only a level change (an artifact), it missed
  // the all-clear of Buchanskyi, and it saw a newer all-clear of Kharkivskyi that the seed does not know yet
  await store.saveLive([
    { unit: "Київ", started: t0 + 4 * H, ended: null, level: "red", max_level: "red" },                         // artifact, after data_end
    { unit: "Бучанський район", started: t0, ended: null, level: "yellow", max_level: "yellow" },
    { unit: "Харківський район", started: t0 + H, ended: t0 + 5 * H, level: "red", max_level: "red" },
    { unit: "Броварський район", started: t0 - H, ended: t0 - H / 2, level: "yellow", max_level: "yellow" }, // not in the seed
    { unit: "Обухівський район", started: t0 + 6 * H, ended: null, level: "yellow", max_level: "yellow" },     // after data_end
  ], { last: 900, ok: t0 + 6 * H, gapTo: 800 });
  const seed = { kind: "alerts", max_post_id: 850, data_end: t0 + 3 * H, since: t0 - 2 * H, alerts: [
    ["Київ", t0 + H, null, "yellow", "yellow"],
    ["Бучанський район", t0, t0 + H, "yellow", "yellow"],
    ["Харківський район", t0 + H, null, "red", "red"],
  ] };
  assert.equal(invalidAlerts(seed), null);
  await store.applySeed(seed, t0 + 6 * H);
  const rows = Object.fromEntries(store.db.raw.prepare("SELECT * FROM alerts").all().map((r) => [`${r.unit}@${(r.started - t0) / H}`, r]));
  assert.deepEqual(Object.keys(rows).sort(), ["Бучанський район@0", "Київ@1", "Обухівський район@6", "Харківський район@1"]);
  assert.equal(rows["Бучанський район@0"].ended, t0 + H);          // the all-clear the Worker missed
  assert.equal(rows["Харківський район@1"].ended, t0 + 5 * H);     // the Worker's newer all-clear stays
  assert.equal(rows["Київ@1"].ended, null);                         // the real start; the artifact at +4 h is gone
  assert.equal(rows["Київ@1"].level, "yellow");                     // the Worker (post 900) is newer than the seed (850)
  const meta = await store.liveMeta();
  assert.equal(meta.last, 900); assert.equal(meta.gapTo, 0);         // the seed reaches post 850, past the gap (800)
  await store.saveLive([], { ...meta, gapTo: 870 });
  await store.applySeed({ ...seed, alerts: [] }, t0 + 6 * H);
  assert.equal((await store.liveMeta()).gapTo, 870);                 // a seed that ends before the gap does not close it
});

// ---------- the bot screens ----------
const LIVE_OK = Date.parse("2026-10-12T11:15:00Z");
const hoursSetup = async ({ open = null, ok = LIVE_OK, gapTo = 0, params = HOURLY.params } = {}) => {
  const env = setup();
  await env.store.addUnit(1, "Київ");
  await env.store.saveHourly("2026-10-11", params);
  await env.store.saveLive(open ? [open] : [], { last: 10, ok, gapTo });
  let t = Date.parse("2026-10-12T11:20:00Z");                        // 14:20 Kyiv
  env.r.now = () => new Date((t += 1000));
  return env;
};
const K = `${catalog.units.indexOf("Київ")}`;
const lastEdit = (tg) => tg.calls.filter((c) => c[0] === "edit").at(-1);

test("🕐 hour risk: one raion -> day -> hours after now, four to a row -> result", async () => {
  const { r, tg } = await hoursSetup();
  await r.handle(msg("/hours"));
  assert.match(tg.calls.at(-1)[2], /Ризик по годинах · Київ/);
  assert.deepEqual(buttons(tg.calls.at(-1)[3]).map((b) => b.callback_data), [`hr:${K}:0`, `hr:${K}:1`, "home"]);
  await r.handle(cb(`hr:${K}:0`));
  const hours = lastEdit(tg)[3].inline_keyboard;
  assert.equal(hours[0][0].text, "15:00");                              // 14:20 now: from 15:00
  assert.ok(hours.slice(0, -1).every((row) => row.length <= 4));
  await r.handle(cb(`hr:${K}:1`));
  assert.equal(buttons(lastEdit(tg)[3]).length, 24 + 1);
  await r.handle(cb(`hr:${K}:1:15`));
  const text = lastEdit(tg)[2];
  assert.match(text, /Київ · завтра, 15:00/);
  assert.match(text, /Зараз: тривоги немає/);
  assert.match(text, /Історично<\/b> о 15:00 тривога була у <b>\d+%<\/b>/); // no alert now: the model never beat history
  assert.match(text, /не сигнал безпеки/);
});

test("🕐 during an alert: shelter first, never a hint to go out; stale data: no claim about now", async () => {
  const open = { unit: "Київ", started: LIVE_OK - 40 * MINUTE, ended: null, level: "red", max_level: "red" };
  let { r, tg } = await hoursSetup({ open });
  await r.handle(cb(`hr:${K}:0:15`));
  let text = lastEdit(tg)[2];
  assert.match(text, /Зараз у районі тривога<\/b> \(червона\)/);
  assert.match(text, /Будьте в укритті й слухайте офіційні сповіщення/);
  assert.match(text, /Ймовірність, що о 15:00 буде тривога: <b>\d+%<\/b>/);  // 1 h ahead with an alert on: the model
  for (const t of [text, ui.hourText("Київ", 0, 15, { clim: 0.01, model: 0.01 }, { on: true, elapsed: 5, level: "yellow" }, LIVE_OK, 180)]) {
    assert.doesNotMatch(t, /можна виходити|виходьте|безпечно|йдіть гуляти/i);
    assert.match(t, /Будьте в укритті/);
  }
  ({ r, tg } = await hoursSetup({ open, ok: LIVE_OK - 60 * MINUTE }));
  await r.handle(cb(`hr:${K}:0:15`));
  text = lastEdit(tg)[2];
  assert.match(text, /Немає свіжих даних про тривоги зараз \(останнє оновлення о 13:15\)/);
  assert.doesNotMatch(text, /Зараз: тривоги немає/);
  assert.match(text, /лише історія/);
  ({ r, tg } = await hoursSetup({ gapTo: 5 }));
  await r.handle(cb(`hr:${K}:0:15`));
  assert.match(lastEdit(tg)[2], /Немає свіжих даних/);
});

test("🕐 forged or stale hour callbacks change nothing and always get an answer", async () => {
  const { r, tg, store } = await hoursSetup();
  const other = `${catalog.units.indexOf("Бучанський район")}`;
  const before = JSON.stringify(await store.get(1));
  for (const d of ["hr:99999", "hr:-1", `hr:${other}`, `hr:${other}:1:10`, `hr:${K}:2`, `hr:${K}:1:24`, `hr:${K}:1:-1`, `hr:${K}:1:²`,
    `hr:${K}:1:1e1`, `hr:${K}:1:15:0`, "hr:abc", `hr:${K}:x:5`, "rc:7", "rc:1:1", `hr:${K}:0:9`]) {
    const n = tg.calls.length;
    await r.handle(cb(d));
    assert.equal(tg.calls.at(-1)[0], "answer", d);
    assert.ok(!tg.calls.slice(n).some((c) => c[0] === "edit" && /🕐 <b>Київ · /.test(c[2] || "")), d);
  }
  assert.equal(tg.calls.at(-1)[2], "Ця година вже минула.");
  assert.equal(JSON.stringify(await store.get(1)), before);
  const fresh = setup();                                               // no raion yet
  await fresh.r.handle(cb(`hr:${K}:1:15`));
  assert.equal(fresh.tg.calls.at(-1)[2], "Спершу оберіть район: /start");
});

test("🕐 several raions: pick one first; late evening: today has no hours left", async () => {
  const { r, tg, store } = await hoursSetup();
  await store.addUnit(1, "Бучанський район");
  await r.handle(cb("hr"));
  assert.deepEqual(buttons(lastEdit(tg)[3]).map((b) => b.text), ["📍 Київ", "📍 Бучанський", "← Назад"]);
  let t = Date.parse("2026-10-12T20:30:00Z");                           // 23:30 Kyiv
  r.now = () => new Date((t += 1000));
  await r.handle(cb(`hr:${K}:0`));
  assert.match(lastEdit(tg)[2], /годин уже не лишилося/);
});

test("📜 recent alerts: last five with start, end and duration, today's count, an alert still on", async () => {
  const { r, tg, store } = await hoursSetup();
  const day0 = Date.parse("2026-10-11T21:00:00Z");                    // 00:00 Kyiv, 12 Oct
  const rows = [
    { unit: "Київ", started: day0 - 3 * 3600000, ended: day0 - 2 * 3600000, level: "yellow", max_level: "yellow" },
    { unit: "Київ", started: day0 - 30 * MINUTE, ended: day0 + 30 * MINUTE, level: "red", max_level: "red" },  // across midnight
    { unit: "Київ", started: day0 + 5 * 3600000, ended: day0 + 6 * 3600000 + 15 * MINUTE, level: "red", max_level: "red" },
    { unit: "Київ", started: day0 + 7 * 3600000, ended: day0 + 7 * 3600000 + 22 * MINUTE, level: "yellow", max_level: "yellow" },
    { unit: "Київ", started: day0 + 13 * 3600000, ended: null, level: "yellow", max_level: "red" },
    { unit: "Київ", started: day0 - 50 * 3600000, ended: day0 - 49 * 3600000, level: null, max_level: null },
  ];
  await store.saveLive(rows, { last: 10, ok: LIVE_OK, gapTo: 0 });
  await r.handle(msg("/recent"));
  const text = tg.calls.at(-1)[2];
  assert.match(text, /Сьогодні: 4 тривоги, разом 3 год 27 хв/);       // 30 + 75 + 22 + 80 (still on, 13:00 to 14:20)
  assert.match(text, /🔴 12\.10 13:00 → <b>триває<\/b> · 1 год 20 хв/); // max level red although now yellow
  assert.match(text, /🔴 12\.10 05:00–06:15 · 1 год 15 хв/);
  assert.match(text, /🔴 11\.10 23:30–00:30 · 1 год/);
  assert.equal((text.match(/^[🔴🟡🚨] /gmu) || []).length, 5);          // the oldest of six is not shown
  assert.match(text, /Це факти, не прогноз/);
  await r.handle(cb("rc:1"));
  assert.match(lastEdit(tg)[2], /Останні тривоги/);
  assert.deepEqual([1, 2, 5, 11, 21, 22, 25, 112].map(ui.alertsWord), ["тривога", "тривоги", "тривог", "тривог", "тривога", "тривоги", "тривог", "тривог"]);
});

test("ingest: hourly parameters and alert history need the secret, are validated, and send nothing", async () => {
  const env = { DB: fakeD1(), TELEGRAM_BOT_TOKEN: "x", WEBHOOK_SECRET: "w", INGEST_SECRET: "i".repeat(32) };
  const post = (body, auth = true) => worker.fetch(new Request("https://x/ingest", { method: "POST", body: JSON.stringify(body),
    headers: auth ? { Authorization: `Bearer ${env.INGEST_SECRET}` } : {} }), env, { waitUntil: () => assert.fail("no delivery") });
  const hourly = { kind: "hourly", ...HOURLY.params };
  assert.equal((await post(hourly, false)).status, 403);
  assert.equal((await post(hourly)).status, 200);
  assert.equal((await post(hourly)).status, 200);                        // latest wins, no 409
  assert.equal((await new Store(env.DB).hourlyParams()).as_of, HOURLY.params.as_of);
  const u0 = Object.keys(hourly.units)[0], unit = (f) => ({ ...hourly, units: { [u0]: { ...hourly.units[u0], ...f } } });
  for (const b of [{ ...hourly, as_of: "2026-13-01" }, { ...hourly, step: 5 }, { ...hourly, units: {} }, { ...hourly, units: { evil: hourly.units[u0] } },
    unit({ clim: [2, ...hourly.units[u0].clim.slice(1)] }), unit({ prof: Array(24).fill(0) }), unit({ clim: hourly.units[u0].clim.slice(1) }),
    { ...hourly, surv: [...hourly.surv.slice(0, 5), 1, ...hourly.surv.slice(6)] }, { ...hourly, surv: hourly.surv.slice(1) },
    { ...hourly, gates: { ...hourly.gates, on: hourly.gates.on.map(String) } }, { ...hourly, gates: { buckets: [[0, 1]], on: [true], off: [true] } }]) {
    assert.equal((await post(b)).status, 400, JSON.stringify(b).slice(0, 120));
  }
  const t = Date.parse("2026-10-12T12:00:00Z");
  const alerts = { kind: "alerts", max_post_id: 5, data_end: t, since: t - 7 * 86400000, alerts: [["Київ", t - 3600000, t - 1800000, "red", "red"], ["Київ", t - 600000, null, "yellow", "red"]] };
  assert.equal((await post(alerts)).status, 200);
  for (const b of [{ ...alerts, max_post_id: -1 }, { ...alerts, since: t + 1 }, { ...alerts, alerts: [["evil", t - 1, null, null, null]] },
    { ...alerts, alerts: [["Київ", t - 1, t - 2, null, null]] }, { ...alerts, alerts: [["Київ", t - 1, null, "green", null]] },
    { ...alerts, alerts: [["Київ", t + 1, null, null, null]] }, { ...alerts, alerts: [["Київ", 1.5, null, null, null]] }]) {
    assert.equal((await post(b)).status, 400, JSON.stringify(b).slice(0, 120));
  }
});

test("schedule: every 5-minute run scrapes first; a failed scrape does not stop delivery", async () => {
  const env = { DB: fakeD1(), TELEGRAM_BOT_TOKEN: "x" };
  const real = globalThis.fetch, urls = [];
  globalThis.fetch = async (url) => { urls.push(String(url)); return new Response(PAGE); };
  try { await worker.scheduled({ cron: "*/5 * * * *", scheduledTime: Date.parse("2026-10-12T18:02:00Z") }, env); } finally { globalThis.fetch = real; }
  assert.deepEqual(urls, ["https://t.me/s/UkraineAlarmSignal"]);
  const meta = await new Store(env.DB).liveMeta();
  assert.equal(meta.last, 654224); assert.equal(meta.gapTo, 654204);    // first run: what came before is unknown

  const store = new Store(env.DB), sent = [];
  await store.addUnit(1, "Київ");
  await store.saveForecast("evening", "2026-10-13", { rows, banner: "" });
  globalThis.fetch = async (url, init) => {
    if (String(url).includes("t.me")) return new Response("blocked", { status: 403 });
    sent.push(JSON.parse(init.body).chat_id); return new Response(JSON.stringify({ ok: true, result: {} }));
  };
  try { await worker.scheduled({ cron: "*/5 * * * *", scheduledTime: Date.parse("2026-10-12T17:05:00Z") }, env); } finally { globalThis.fetch = real; }
  assert.deepEqual(sent, [1]);                                           // 20:05 Kyiv: the forecast still went out
});

test("new keyboards fit a phone", () => {
  const index = Object.fromEntries(catalog.units.map((u, i) => [u, i]));
  for (const b of [ui.hoursUnitKeyboard(["Білоцерківський район", "Київ"], index), ui.hoursDayKeyboard(1, true), ui.hoursKeyboard(1, 1, 0),
    ui.hourResultKeyboard(1, 0), ui.recentKeyboard(), ui.doneKeyboard(), ui.welcomeBackKeyboard({ active: 1, units: ["Київ"] })]) {
    for (const row of b.inline_keyboard) {
      for (const x of row) { assert.ok(x.text.length <= 30, x.text); assert.ok(x.callback_data.length <= 64); }
      if (row.length === 2) assert.ok(row.every((x) => x.text.length <= 16), row.map((x) => x.text));
      if (row.length > 2) assert.ok(row.length <= 4 && row.every((x) => x.text.length <= 5), row.map((x) => x.text));
    }
  }
});

test("every screen reached by a button has a way back; ← Назад leads to the menu; removing a raion asks first", async () => {
  const { r, tg, store } = await hoursSetup();
  const index = Object.fromEntries(catalog.units.map((u, i) => [u, i]));
  const s = await store.get(1);
  const backish = (k) => buttons(k).some((b) => /^← /.test(b.text));
  const boards = { oblasts: ui.oblastKeyboard(Object.keys(catalog.oblasts)), raions: ui.raionKeyboard(catalog.oblasts["Київська область"], index),
    search: ui.searchKeyboard(search("Буча"), index), how: ui.howKeyboard(), help: ui.helpKeyboard(), settings: ui.settingsKeyboard(s),
    units: ui.unitsKeyboard(s.units, index, true), hoursUnit: ui.hoursUnitKeyboard(["Київ", "Бучанський район"], index),
    hoursDay: ui.hoursDayKeyboard(1, false), hours: ui.hoursKeyboard(1, 1), result: ui.hourResultKeyboard(1, 0), recent: ui.recentKeyboard(),
    remove: ui.removeConfirm("Київ", 1)[1], ...Object.fromEntries(Object.keys(ui.SCREENS).map((n) => [n, ui.screen(n, s)[1]])) };
  for (const [name, k] of Object.entries(boards)) assert.ok(backish(k), `${name} has no way back`);
  await r.handle(cb("home"));
  assert.match(lastEdit(tg)[2], /Меню/);
  assert.deepEqual(buttons(lastEdit(tg)[3]).map((b) => b.callback_data), ["now", "hr", "rc", "s:root", "add", "help"]);
  const fresh = setup();
  await fresh.r.handle(cb("home"));
  assert.match(lastEdit(fresh.tg)[2], /Шкільний ранок/);                // no raion yet: the welcome screen
  await r.handle(cb("add"));
  assert.equal(buttons(lastEdit(tg)[3]).at(-1).callback_data, "s:units"); // adding from settings goes back to settings
  await r.handle(cb(`rm:${K}`));
  assert.match(lastEdit(tg)[2], /Прибрати <b>Київ<\/b>/);
  assert.deepEqual((await store.get(1)).units, ["Київ"]);                 // nothing removed by one tap
  await r.handle(cb(`rmy:${catalog.units.indexOf("Бучанський район")}`)); // not followed: refused
  assert.deepEqual((await store.get(1)).units, ["Київ"]);
  await r.handle(cb(`rmy:${K}`));
  assert.deepEqual((await store.get(1)).units, []);
  for (const d of ["home:1", "rmy:abc", "rm:99999"]) { await r.handle(cb(d)); assert.equal(tg.calls.at(-1)[0], "answer"); }
});
