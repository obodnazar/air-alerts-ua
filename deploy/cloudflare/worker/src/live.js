// The Worker's own view of alerts "now": every 5 minutes it reads the public web preview of @UkraineAlarmSignal
// (t.me/s, no login, like src/airalerts/sources/telegram.py) and keeps open and recent alerts per raion in D1.
// Budget per run: at most PAGES fetches (Free plan: 50 subrequests) and 4 D1 queries; parsing a page takes ~0.1 ms of
// CPU warm, ~2 ms cold (test "signal: CPU per scrape" checks it against the 10 ms Free limit).
import { parsePage, toEvents, applyEvents } from "./signal.js";

export const CHANNEL = "https://t.me/s/UkraineAlarmSignal";
export const PAGES = 5;            // 100 posts; the busiest 5 minutes since September had 28
export const STALE_MS = 15 * 60000; // older than this, "now" is unknown and the bot says so
const DAY_MS = 86400000;
const UA = "shkilnyi-ranok-bot/1.0 (+https://github.com/obodnazar/air-alerts-ua)";

/** One scrape run: fetch posts newer than the last one processed, apply them, save. Throws on a page with no posts
 *  (a block page or a changed layout is not "no news"), so the freshness stamp is not renewed on old data. */
export async function scrape(store, fetchFn, units, now = Date.now(), pages = PAGES) {
  const meta = await store.liveMeta();
  const serve = new Set(units);
  const fresh = [];
  let before = null, reached = false, oldestSeen = null;
  for (let p = 0; p < pages; p++) {
    const res = await fetchFn(before ? `${CHANNEL}?before=${before}` : CHANNEL, { headers: { "User-Agent": UA } });
    if (!res.ok) throw new Error(`t.me: HTTP ${res.status}`);
    const posts = parsePage(await res.text());
    if (!posts.length) {
      if (p === 0) throw new Error("t.me: no posts parsed from the live page");
      break; // an empty older page is a block page, not the start of the channel: keep the gap (audit round 3)
    }
    if (posts.some((x) => x.unreadable)) throw new Error("t.me: a post without text or media; layout changed?");
    const oldest = Math.min(...posts.map((x) => x.id));
    oldestSeen = oldest;
    const newer = posts.filter((x) => x.id > meta.last && (before === null || x.id < before));
    fresh.push(...newer);
    if (!meta.last || oldest <= meta.last + 1 || !newer.length) { reached = !!meta.last; break; }
    before = oldest;
  }
  // first run, or more new posts than the page budget: what came before is unknown until the next alert history
  // from /ingest covers it (gapTo)
  let gapTo = meta.gapTo;
  if (!reached && oldestSeen) gapTo = Math.max(gapTo, oldestSeen - 1);
  fresh.sort((a, b) => a.id - b.id);
  const last = fresh.length ? Math.max(meta.last, fresh.at(-1).id) : meta.last;
  let changed = [];
  if (fresh.length) {
    const open = {};
    for (const r of await store.openAlerts()) (open[r.unit] ||= []).push(r);
    changed = applyEvents(open, toEvents(fresh), (u) => serve.has(u));
  }
  // compare-and-swap on the cursor: if another run (or /ingest) moved it meanwhile, this run's view is old; drop it
  const applied = await store.saveLive(changed, { last, ok: now, gapTo }, meta.last);
  return { posts: fresh.length, changed: applied ? changed.length : 0, gap: gapTo > 0, applied };
}

/** What the bot may say about "now" in a raion: null if the live data are stale or incomplete. */
export function liveState(meta, open, now = Date.now()) {
  if (!meta.ok || now - meta.ok > STALE_MS || meta.gapTo > 0) return null;
  if (!open) return { on: false, elapsed: 0, level: null };
  return { on: true, elapsed: Math.max(0, (now - open.started) / 60000), level: open.level, started: open.started,
    stuck: now - open.started > DAY_MS };
}
