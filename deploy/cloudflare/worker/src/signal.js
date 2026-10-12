// @UkraineAlarmSignal web preview -> alert events -> open/closed alerts per raion.
// Ported from src/airalerts/sources/telegram.py (parse_page) and src/airalerts/parse_signal.py (sections, units,
// episode pairing). test/fixtures/signal_parity.json holds 600 real posts and what Python makes of them.

const POST_SPLIT = '<div class="tgme_widget_message_wrap';
const NAMED = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };

/** html.unescape for what t.me emits: numeric entities and the few named ones. */
export function unescapeHtml(s) {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
    if (e[0] === "#") {
      const n = e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      return Number.isFinite(n) && n > 0 && n <= 0x10ffff ? String.fromCodePoint(n) : m;
    }
    return NAMED[e.toLowerCase()] ?? m;
  });
}
const clean = (frag) => unescapeHtml(frag.replace(/<br\s*\/?>/g, "\n").replace(/<[^>]+>/g, "")).trim();

/** Posts on one t.me/s page: [{id, published_at (ms), text}], oldest first. */
export function parsePage(html) {
  const out = [];
  const chunks = html.split(POST_SPLIT);
  for (let i = 1; i < chunks.length; i++) {
    const c = chunks[i];
    const id = /data-post="[^/"]+\/(\d+)"/.exec(c), t = /<time datetime="([^"]+)"/.exec(c);
    if (!id || !t) continue;
    const at = Date.parse(t[1]);
    if (!Number.isFinite(at)) continue;
    const body = /<div class="tgme_widget_message_text[^"]*"[^>]*>([\s\S]*?)<\/div>/.exec(c);
    out.push({ id: Number(id[1]), published_at: at, text: body ? clean(body[1]) : "" });
  }
  return out;
}

// ---------- parse_signal.py ----------
const HEADERS = new Set(["відбій тривоги", "червоний рівень тривоги", "жовтий рівень тривоги", "повітряна тривога",
  "відбій червоної тривоги", "відбій червоної тривоги, жовта тривога ще триває"]);
const UNIT_RE = /^(?:•\s*)?([^()\n•]+?)\s*(?:\(([^)]+?)\s*обл\.\))?\s*$/u;
const SECTION = /\n\s*\n/;
export const norm = (name) => name.replace(/[ʼ’`]/g, "'").replace(/\s+/g, " ").trim();

export function unitType(name) {
  if (name === "Київ") return "kyiv";
  if (name.endsWith("район")) return "raion";
  if (name.includes("громада")) return "hromada";
  return "city";
}

/** [kind, level]: kind in start / end / downgrade / threat / ignore. */
export function classify(text) {
  const t = text.toLowerCase();
  if (t.startsWith("мапа тривог") || t.includes("змі повідомляють")) return ["ignore", null];
  if (t.includes("відбій червоної тривоги")) return ["downgrade", "yellow"];
  if (t.includes("відбій тривоги")) return ["end", null];
  if (t.includes("червоний рівень")) return ["start", "red"];
  if (t.includes("жовтий рівень")) return ["start", "yellow"];
  if (t.includes("повітряна тривога")) return ["start", null];
  if (t.includes("загроза")) return ["threat", null];
  return ["ignore", null];
}

/** [[name, oblast]] named in one section. */
export function unitsIn(text) {
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  if (!lines.length) return [];
  const first = lines[0].replace(/^[^\p{L}\p{N}_]+/u, "").trim();
  const cands = HEADERS.has(first.toLowerCase())
    ? lines.slice(1).filter((l) => UNIT_RE.test(l) && (l.includes("обл.") || l.includes("Київ")))
    : [first];
  const out = [];
  for (const c of cands) {
    const m = UNIT_RE.exec(c);
    if (!m) continue;
    const name = norm(m[1]);
    let oblast = m[2] ? `${norm(m[2])} область` : null;
    if (name === "Київ") oblast = "м. Київ";
    out.push([name, oblast]);
  }
  return out;
}

/** One event per (post, section, unit), in post order; threats and ignored sections are dropped. */
export function toEvents(posts) {
  const out = [];
  for (const p of [...posts].sort((a, b) => a.published_at - b.published_at || a.id - b.id)) {
    for (const section of p.text.split(SECTION)) {
      if (!section.trim()) continue;
      const [kind, level] = classify(section);
      if (!["start", "end", "downgrade"].includes(kind)) continue;
      for (const [unit, oblast] of unitsIn(section)) out.push({ post: p.id, at: p.published_at, unit, oblast, kind, level });
    }
  }
  return out;
}

const RANK = { null: 0, yellow: 1, red: 2 };
const maxLevel = (a, b) => (RANK[b ?? null] > RANK[a ?? null] ? b : a);

/** Apply events to the open alerts ({unit: [rows]}, rows = {unit, started, ended, level, max_level}), as
 *  parse_signal.to_episodes pairs them: a start while open is a level change, an all-clear closes the alert.
 *  Returns the rows that changed or were created. `keep(unit)` filters units (raions and Kyiv we serve). */
export function applyEvents(open, events, keep = () => true) {
  const changed = new Map();
  const touch = (r) => changed.set(`${r.unit}\u0000${r.started}`, r);
  for (const e of events) {
    if (!keep(e.unit)) continue;
    const cur = open[e.unit] || [];
    if (e.kind === "start") {
      if (!cur.length) {
        const r = { unit: e.unit, started: e.at, ended: null, level: e.level, max_level: e.level };
        open[e.unit] = [r]; touch(r);
      } else for (const r of cur) { r.level = e.level; r.max_level = maxLevel(r.max_level, e.level); touch(r); }
    } else if (e.kind === "downgrade") {
      for (const r of cur) { r.level = "yellow"; touch(r); }
    } else if (e.kind === "end") {
      for (const r of cur) { r.ended = e.at; touch(r); }
      delete open[e.unit];
    }
  }
  return [...changed.values()];
}
