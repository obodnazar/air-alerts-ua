// Cloudflare Worker entry: Telegram webhook, forecast ingest, and the 5-minute delivery schedule.
import { Store } from "./store.js";
import { Telegram, Blocked, RateLimited } from "./telegram.js";
import { Router } from "./router.js";
import { catalog } from "./catalog.js";
import * as ui from "./ui.js";
import { liveState } from "./live.js";
import { kyivNow, addDays, weekday, plusMinutes } from "./util.js";
import { scrape } from "./live.js";

const SLOTS = { evening: ["20:00", "20:30", "21:00"], morning: ["08:05"] };
const LATE = 90;        // minutes a slot may still go out after its time
const TG_BUDGET = 40;   // Workers Free: 50 external subrequests per invocation, shared by all slots of one run
                        // and the live scrape (≤ PAGES = 5 fetches): 45 at most
const CLAIM = 200;      // rows claimed per slot and run; those not sent (budget spent) go back to pending
const SETTLE_EVERY = 10;          // write delivery progress every 10 sends (at most 4 extra D1 queries per run)
const TIME_BUDGET_MS = 20000;     // stop sending after 20 s; waitUntil after /ingest is cut ~30 s after the response
const COMMANDS = [{ command: "now", description: "🌙 прогноз на найближчий шкільний день" },
  { command: "settings", description: "⚙️ райони, контрольна точка, час, стиль порад" },
  { command: "hours", description: "🕐 ризик тривоги на обрану годину" }, { command: "recent", description: "📜 останні тривоги" },
  { command: "start", description: "📍 обрати або додати район" }, { command: "pause", description: "⏸ пауза на канікули" },
  { command: "help", description: "❓ команди і як читати прогноз" }, { command: "stop", description: "🔕 відписатися" }];
// shown in the empty chat before /start ("What can this bot do?") and on the profile
const SHORT = "Прогноз тривог для шкільного ранку: чи буде тривога о 08:00 і чи встигне відбій до уроків.";
const LONG = "🌅 Шкільний ранок: прогноз повітряних тривог для шкільного дня.\n\n" +
  "🌙 Щовечора близько 20:05: чи буде тривога о 08:00 у вашому районі і чи встигне відбій до контрольної точки школи (правило МОН).\n" +
  "🌅 О 08:05, якщо тривога: чекати відбою чи переходити на дистанційку.\n\n" +
  "Некомерційний студентський проєкт, модель перевірена на вересні–жовтні 2026. " +
  "Це прогноз для планування, а не сигнал безпеки: під час тривоги йдіть в укриття.";

/** Compare secrets in constant time: hash both (equal length), then XOR every byte. */
async function sameSecret(given, expected) {
  if (!given || !expected) return false;
  const enc = new TextEncoder();
  const [x, y] = await Promise.all([given, expected].map((v) => crypto.subtle.digest("SHA-256", enc.encode(v))));
  const a = new Uint8Array(x), b = new Uint8Array(y);
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a[i] ^ b[i];
  return d === 0;
}
/** Strict "Authorization: Bearer <secret>". */
const bearer = (req) => /^Bearer (\S+)$/.exec(req.headers.get("Authorization") || "")?.[1] || "";

const isDay = (d) => typeof d === "string" && /^\d{4}-\d{2}-\d{2}$/.test(d) && !isNaN(Date.parse(`${d}T00:00:00Z`))
  && new Date(`${d}T00:00:00Z`).toISOString().slice(0, 10) === d; // rejects 2026-02-30
const isP = (v) => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1;
const CPS = ["p_clear_9", "p_clear_10", "p_clear_11", "p_clear_12"];
const FIELDS = { evening: ["p_alert_08", "usual", ...CPS], morning: CPS };

/** The /ingest body, checked field by field; returns an error string or null. Mirrors forecasts.py's tables. */
export function invalid(body, units = new Set(catalog.units)) {
  if (!body || typeof body !== "object") return "body";
  if (typeof body.kind !== "string" || !Object.hasOwn(FIELDS, body.kind)) return "kind"; // not "toString"
  if (body.replace !== undefined && typeof body.replace !== "boolean") return "replace";
  if (!isDay(body.day)) return "day";
  if (!Array.isArray(body.rows) || body.rows.length > units.size) return "rows";
  if (body.kind === "evening" && !body.rows.length) return "empty evening forecast"; // a morning with no alerts is valid
  const seen = new Set();
  for (const r of body.rows) {
    if (!r || typeof r !== "object" || !units.has(r.unit) || seen.has(r.unit)) return `unit ${String(r?.unit).slice(0, 40)}`;
    seen.add(r.unit);
    for (const f of FIELDS[body.kind]) if (!isP(r[f])) return `${r.unit}: ${f}`;
    if (body.kind === "morning" && (!["red", "yellow"].includes(r.level) || !(Number.isFinite(r.elapsed_min) && r.elapsed_min >= 0))) {
      return `${r.unit}: level/elapsed_min`;
    }
  }
  if (body.banner != null && typeof body.banner !== "string") return "banner";
  return null;
}

const GRID = 288; // hourly.py: 48 h on a 10-minute grid
const isNum = (v, lo = -Infinity, hi = Infinity) => typeof v === "number" && Number.isFinite(v) && v >= lo && v <= hi;
const isArr = (a, n, ok) => Array.isArray(a) && a.length === n && a.every(ok);
const LEVELS = new Set(["red", "yellow", null]);

/** kind "hourly": per-raion parameters of the hour model (hourly.payload in Python). Error string or null. */
export function invalidHourly(b, units = new Set(catalog.units)) {
  if (!isDay(b.as_of)) return "as_of";
  if (b.step !== 10) return "step";
  if (!b.units || typeof b.units !== "object" || Array.isArray(b.units)) return "units";
  const names = Object.keys(b.units);
  if (!names.length || names.length > units.size) return "units";
  for (const u of names) {
    const r = b.units[u];
    if (!units.has(u) || !r || !isArr(r.clim, 24, (v) => isNum(v, 0, 1)) || !isArr(r.prof, 24, (v) => isNum(v, 0, 1))
      || !(r.prof.reduce((a, v) => a + v, 0) > 0)) return `unit ${u.slice(0, 40)}`;
  }
  if (!isArr(b.surv, GRID + 1, (v) => isNum(v, 0, 1)) || b.surv.some((v, i) => i && v > b.surv[i - 1])) return "surv";
  const g = b.gates;
  if (!g || !Array.isArray(g.buckets) || !g.buckets.length || g.buckets.length > 24
    || !g.buckets.every((x) => isArr(x, 2, (v) => Number.isInteger(v) && v >= 1 && v <= 48) && x[0] <= x[1])) return "gates";
  for (const k of ["on", "off"]) if (!isArr(g[k], g.buckets.length, (v) => typeof v === "boolean")) return "gates";
  return null;
}

/** kind "alerts": alert history per raion from the Python parser, to heal the live state. Error string or null. */
export function invalidAlerts(b, units = new Set(catalog.units)) {
  if (!Number.isInteger(b.max_post_id) || b.max_post_id <= 0) return "max_post_id";
  if (!isNum(b.data_end, 1.7e12, 4e12) || !isNum(b.since, 1.7e12, b.data_end)) return "data_end/since";
  if (!Array.isArray(b.alerts) || b.alerts.length > 20000) return "alerts";
  // D1 binds one string of at most 2 000 000 bytes; the history goes in as one JSON parameter (audit round 4)
  if (new TextEncoder().encode(JSON.stringify(b.alerts)).length > 1_500_000) return "alerts too large";
  for (const a of b.alerts) {
    if (!Array.isArray(a) || a.length !== 5 || !units.has(a[0]) || !Number.isInteger(a[1]) || a[1] < b.since || a[1] > b.data_end
      || !(a[2] === null || (Number.isInteger(a[2]) && a[2] >= a[1] && a[2] <= b.data_end)) || !LEVELS.has(a[3]) || !LEVELS.has(a[4])) {
      return `alert ${String(a?.[0]).slice(0, 40)}`;
    }
  }
  return null;
}

/** Which (kind, day, slot) are due now, given Kyiv local time. Pure, tested. */
export function dueSlots(now = new Date()) {
  const k = kyivNow(now), out = [];
  for (const [kind, slots] of Object.entries(SLOTS)) {
    const day = kind === "evening" ? addDays(k.day, 1) : k.day;
    if (weekday(day) >= 5) continue; // no school on Sat/Sun
    for (const slot of slots) if (slot <= k.hhmm && k.hhmm <= plusMinutes(slot, LATE)) out.push({ kind, day, slot });
  }
  return out;
}

/** Deliver one slot from the outbox. `budget.calls` is shared by every slot of the run (Telegram subrequests);
 *  a 429 stops the whole run. Anything not sent stays pending for the next tick (every 5 minutes). */
export async function deliver(store, tg, { kind, day, slot }, budget = { calls: TG_BUDGET }, now = Date.now()) {
  const f = await store.forecast(kind, day);
  if (!f) return { sent: 0, waiting: true };
  await store.plan(kind, day, slot);
  if (budget.calls <= 0 || budget.stop) return { sent: 0 };
  const token = crypto.randomUUID();
  const subs = await store.claim(kind, day, slot, token, now, CLAIM);
  if (!subs.length) return { sent: 0 };
  const rows = Object.fromEntries(f.rows.map((r) => [r.unit, r]));
  // morning: what is true at sending time, not only at 08:00 (two D1 queries, once per slot and run)
  let live = {};
  if (kind === "morning") {
    const [meta, open] = await Promise.all([store.liveMeta(), store.openAlerts()]);
    const byUnit = Object.fromEntries(open.map((r) => [r.unit, r]));
    const units = new Set(subs.flatMap((s) => s.units));
    for (const u of units) live[u] = liveState(meta, byUnit[u], now);
  }
  const outcome = {}, started = Date.now();
  let sent = 0, done = {};
  for (const sub of subs) {
    const text = kind === "evening" ? ui.eveningMessage(sub, rows, day, f.banner)
      : ui.morningMessage(sub, rows, day, f.banner, live);
    if (!text) { outcome[sub.chat_id] = "skipped"; continue; } // e.g. morning without an alert in their raions
    // budget: Telegram calls shared by the run, and time (waitUntil after /ingest has ~30 s); the rest stays pending
    if (budget.calls <= 0 || budget.stop || Date.now() - started > TIME_BUDGET_MS) continue;
    budget.calls--;
    try { await tg.send(sub.chat_id, text); outcome[sub.chat_id] = done[sub.chat_id] = "sent"; sent++; }
    catch (e) {
      if (e instanceof Blocked) outcome[sub.chat_id] = done[sub.chat_id] = "blocked";
      else if (e instanceof RateLimited) budget.stop = true;  // Telegram asks us to slow down: stop the run
      // network or 5xx: stays pending, retried on the next tick
    }
    if (Object.keys(done).length >= SETTLE_EVERY) { await store.settle(token, done); done = {}; } // progress survives a cut
  }
  await store.finish(token, outcome);
  return { sent, claimed: subs.length };
}

/** One delivery run over every slot due now, with one shared budget. Called by the 5-minute cron and right after
 *  /ingest, so a forecast that arrives at 20:02 goes out at 20:02, not at the next tick. */
export async function runDue(env, now = Date.now()) {
  const store = new Store(env.DB), tg = new Telegram(String(env.TELEGRAM_BOT_TOKEN || "").trim(), undefined, undefined, { maxWait: 0, tries: 1 });
  const budget = { calls: TG_BUDGET };
  for (const slot of dueSlots(new Date(now))) {
    const r = await deliver(store, tg, slot, budget, now);
    if (r.sent || r.waiting) console.log(JSON.stringify({ ...slot, ...r }));
  }
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/telegram") {
      if (!(await sameSecret(request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "", env.WEBHOOK_SECRET))) {
        return new Response("forbidden", { status: 403 });
      }
      const update = await request.json().catch(() => null);
      if (update) {
        const router = new Router(new Store(env.DB), catalog, new Telegram(String(env.TELEGRAM_BOT_TOKEN || "").trim()));
        try { await router.handle(update); } catch (e) {
          console.log("handler error", e.message);
          // the database is down (e.g. D1's daily free limit): say so instead of silence; this path needs no database
          const chat = update.message?.chat?.id ?? update.callback_query?.message?.chat?.id;
          if (chat && /D1_ERROR|D1 /.test(String(e.message))) {
            if (update.callback_query) await router.tg.answer(update.callback_query.id).catch(() => {});
            await router.tg.send(chat, ui.DB_DOWN).catch(() => {});
          }
        }
      }
      return new Response("ok"); // always 200, so Telegram does not re-deliver the same update forever
    }
    if (request.method === "POST" && url.pathname === "/ingest") {
      if (!(await sameSecret(bearer(request), env.INGEST_SECRET))) return new Response("forbidden", { status: 403 });
      const body = await request.json().catch(() => null);
      const store = new Store(env.DB);
      if (body?.kind === "hourly" || body?.kind === "alerts") {
        const bad = body.kind === "hourly" ? invalidHourly(body) : invalidAlerts(body);
        if (bad) return Response.json({ ok: false, error: bad }, { status: 400 });
        if (body.kind === "hourly") {
          await store.saveHourly(body.as_of, { as_of: body.as_of, step: body.step, units: body.units, surv: body.surv, gates: body.gates });
          return Response.json({ ok: true, units: Object.keys(body.units).length });
        }
        return Response.json({ ok: true, ...(await store.applySeed(body)) });
      }
      const bad = invalid(body);
      if (bad) return Response.json({ ok: false, error: bad }, { status: 400 });
      // first version wins: the workflow fires at two UTC times (summer/winter), and every subscriber of a day
      // must get the same numbers. A manual re-run passes replace: true.
      // atomic: the insert itself decides who was first, so two racing POSTs cannot both win (audit round 3)
      const saved = await store.saveForecast(body.kind, body.day, { rows: body.rows, banner: String(body.banner || "").slice(0, 500),
        data_end: String(body.data_end || "").slice(0, 40), checked_at: String(body.checked_at || "").slice(0, 40) }, body.replace === true);
      if (!saved) return Response.json({ ok: true, kept: "existing" }, { status: 409 });
      ctx?.waitUntil?.(runDue(env).catch((e) => console.log("delivery after ingest failed", e.message)));
      return Response.json({ ok: true, rows: body.rows.length });
    }
    if (request.method === "POST" && url.pathname === "/setup") {
      // the Worker registers its own webhook with its own token: the token never leaves Cloudflare
      if (!(await sameSecret(bearer(request), env.INGEST_SECRET))) return new Response("forbidden", { status: 403 });
      const token = String(env.TELEGRAM_BOT_TOKEN || "").trim();
      try {
        const tg = new Telegram(token);
        const me = await tg.call("getMe", {});
        await tg.call("setWebhook", { url: `${url.origin}/telegram`, secret_token: env.WEBHOOK_SECRET,
          allowed_updates: ["message", "callback_query"], drop_pending_updates: false });
        await tg.call("setMyCommands", { commands: COMMANDS });
        await tg.call("setMyShortDescription", { short_description: SHORT });
        await tg.call("setMyDescription", { description: LONG });
        const info = await tg.call("getWebhookInfo", {});
        return Response.json({ ok: true, bot: me.username, webhook: info.url, pending: info.pending_update_count });
      } catch (e) {
        // our own messages never contain the token; redact anyway in case a runtime error quotes it
        const safe = String(e?.message || "error").split(token || "\u0000").join("<token>").slice(0, 200);
        return Response.json({ ok: false, error: safe }, { status: 502 });
      }
    }
    if (url.pathname === "/health") {
      // what monitoring needs, at ~3 row reads: is the database readable, how old is the live scrape and the last ingest
      try {
        const store = new Store(env.DB), now = Date.now();
        const [meta, last] = await Promise.all([store.liveMeta(),
          env.DB.prepare("SELECT max(received) AS r FROM forecasts").first()]);
        const liveAgeMin = meta.ok ? Math.round((now - meta.ok) / 60000) : null;
        const ingestAgeH = last?.r ? Math.round((now - Date.parse(last.r)) / 3600000) : null;
        const ok = liveAgeMin !== null && liveAgeMin <= 30 && ingestAgeH !== null && ingestAgeH <= 26; // hourly parameters arrive every day, weekends too
        return Response.json({ ok, db: true, live_age_min: liveAgeMin, ingest_age_h: ingestAgeH }, { status: ok ? 200 : 503 });
      } catch (e) {
        return Response.json({ ok: false, db: false, error: String(e?.message || e).slice(0, 120) }, { status: 503 });
      }
    }
    return new Response("not found", { status: 404 });
  },

  // One cron for both jobs: a second schedule (minutes 2, 7, 12…) was deployed on 12 Oct and never fired.
  // The scrape goes first and cannot block delivery: its failure is logged, and live_ok is not renewed.
  async scheduled(event, env) {
    try {
      const r = await scrape(new Store(env.DB), (...a) => fetch(...a), catalog.units, event.scheduledTime);
      if (r.posts || r.gap) console.log(JSON.stringify({ scrape: r }));
    } catch (e) { console.log("scrape failed", String(e?.message || e).slice(0, 200)); } // the bot then says "no fresh data"
    await runDue(env, event.scheduledTime);
    const k = kyivNow(new Date(event.scheduledTime));
    if (k.hhmm >= "03:00" && k.hhmm < "03:05") await new Store(env.DB).cleanup(k.day).catch((e) => console.log("cleanup failed", e.message));
  },
};
