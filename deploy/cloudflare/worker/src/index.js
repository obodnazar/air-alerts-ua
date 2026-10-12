// Cloudflare Worker entry: Telegram webhook, forecast ingest, and the 5-minute delivery schedule.
import { Store } from "./store.js";
import { Telegram, Blocked } from "./telegram.js";
import { Router } from "./router.js";
import { catalog } from "./catalog.js";
import * as ui from "./ui.js";
import { kyivNow, addDays, weekday, plusMinutes } from "./util.js";

const SLOTS = { evening: ["20:00", "20:30", "21:00"], morning: ["08:05"] };
const LATE = 90;        // minutes a slot may still go out after its time
const BATCH = 40;       // Workers Free: 50 external subrequests per invocation
const COMMANDS = [{ command: "start", description: "обрати район" }, { command: "now", description: "прогноз на завтра" },
  { command: "settings", description: "налаштування" }, { command: "pause", description: "пауза на канікули" },
  { command: "stop", description: "відписатися" }, { command: "help", description: "як це працює" }];

function constantTimeEqual(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
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

/** Send one batch for a slot. Marks after success; transient failures are retried on the next tick. */
export async function deliver(store, tg, { kind, day, slot }, batch = BATCH) {
  if (await store.kv(`done:${kind}:${slot}:${day}`)) return { sent: 0, done: true };
  const f = await store.forecast(kind, day);
  if (!f) return { sent: 0, done: false, waiting: true };
  const rows = Object.fromEntries((f.rows || []).map((r) => [r.unit, r]));
  let sent = 0, attempts = 0, failed = 0;
  for (const sub of await store.active(day)) {
    if (kind === "evening" && sub.evening !== slot) continue;
    if (attempts >= batch) return { sent, done: false }; // the rest go on the next tick
    if (!(await store.markSent(sub.chat_id, kind, day))) continue;
    const text = kind === "evening" ? ui.eveningMessage(sub, rows, day, f.banner) : ui.morningMessage(sub, rows, day, f.banner);
    if (!text) continue;
    attempts++;
    try { await tg.send(sub.chat_id, text); sent++; }
    catch (e) {
      if (e instanceof Blocked) await store.set(sub.chat_id, "active", 0);
      else { await store.unmarkSent(sub.chat_id, kind, day); failed++; }
    }
  }
  if (!failed) await store.kv(`done:${kind}:${slot}:${day}`, "1");
  return { sent, done: !failed };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/telegram") {
      if (!constantTimeEqual(request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "", env.WEBHOOK_SECRET)) {
        return new Response("forbidden", { status: 403 });
      }
      const update = await request.json().catch(() => null);
      if (update) {
        const router = new Router(new Store(env.DB), catalog, new Telegram(env.TELEGRAM_BOT_TOKEN));
        try { await router.handle(update); } catch (e) { console.log("handler error", e.message); }
      }
      return new Response("ok"); // always 200, so Telegram does not re-deliver the same update forever
    }
    if (request.method === "POST" && url.pathname === "/ingest") {
      const auth = (request.headers.get("Authorization") || "").replace(/^Bearer /, "");
      if (!constantTimeEqual(auth, env.INGEST_SECRET)) return new Response("forbidden", { status: 403 });
      const body = await request.json().catch(() => null);
      if (!body || !["evening", "morning"].includes(body.kind) || !/^\d{4}-\d{2}-\d{2}$/.test(body.day) || !Array.isArray(body.rows)) {
        return new Response("bad request", { status: 400 });
      }
      await new Store(env.DB).saveForecast(body.kind, body.day, { rows: body.rows, banner: String(body.banner || "").slice(0, 500), data_end: body.data_end });
      return Response.json({ ok: true, rows: body.rows.length });
    }
    if (request.method === "POST" && url.pathname === "/setup") {
      // the Worker registers its own webhook with its own token: the token never leaves Cloudflare
      const auth = (request.headers.get("Authorization") || "").replace(/^Bearer /, "");
      if (!constantTimeEqual(auth, env.INGEST_SECRET)) return new Response("forbidden", { status: 403 });
      const tg = new Telegram(env.TELEGRAM_BOT_TOKEN);
      await tg.call("setWebhook", { url: `${url.origin}/telegram`, secret_token: env.WEBHOOK_SECRET,
        allowed_updates: ["message", "callback_query"], drop_pending_updates: false });
      await tg.call("setMyCommands", { commands: COMMANDS });
      const info = await tg.call("getWebhookInfo", {});
      return Response.json({ ok: true, webhook: info.url, pending: info.pending_update_count });
    }
    if (url.pathname === "/health") return new Response("ok");
    return new Response("not found", { status: 404 });
  },

  async scheduled(event, env, ctx) {
    const store = new Store(env.DB), tg = new Telegram(env.TELEGRAM_BOT_TOKEN);
    for (const slot of dueSlots(new Date(event.scheduledTime))) {
      const r = await deliver(store, tg, slot);
      if (r.sent || !r.done) console.log(JSON.stringify({ ...slot, ...r }));
    }
  },
};
