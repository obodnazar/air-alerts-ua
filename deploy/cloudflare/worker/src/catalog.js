// Town / raion search, ported from src/airalerts/botapp/catalog.py (incl. its audit fixes).
import CAT from "../../../../data/snapshot/bot_catalog.json" with { type: "json" };

export const catalog = CAT;

export function norm(s) {
  let t = s.toLowerCase().replace(/[ʼ’`]/g, "'");
  // Python's \b...\b: whole words only ("Красносільська" keeps "сільська"), and "м." only before a letter ("м.Київ")
  t = t.replace(/(?<![\p{L}\p{N}_])(територіальна громада|міська|селищна|сільська|громада|район)(?![\p{L}\p{N}_])/gu, " ");
  t = t.replace(/(?<![\p{L}\p{N}_])м\.(?=[\p{L}\p{N}_])/gu, " ");
  return t.replace(/[^a-zа-яіїєґ']+/gu, "");
}

// Ratcliff/Obershelp similarity, the algorithm behind Python's difflib.SequenceMatcher.ratio()
function matches(a, b) {
  if (!a.length || !b.length) return 0;
  let best = 0, ai = 0, bj = 0;
  for (let i = 0; i < a.length; i++) {
    for (let j = 0; j < b.length; j++) {
      let k = 0;
      while (i + k < a.length && j + k < b.length && a[i + k] === b[j + k]) k++;
      if (k > best) { best = k; ai = i; bj = j; }
    }
  }
  if (!best) return 0;
  return best + matches(a.slice(0, ai), b.slice(0, bj)) + matches(a.slice(ai + best), b.slice(bj + best));
}
export const ratio = (a, b) => (a.length + b.length ? (2 * matches(a, b)) / (a.length + b.length) : 1);

export function search(query, cat = CAT, n = 6) {
  const q = norm(query);
  if (q.length < 3) return [];
  const units = new Set(cat.units);
  const scored = [];
  for (const u of cat.units) {
    const k = norm(u);
    const r = ratio(q, k) + (k.startsWith(q) ? 0.1 : 0);
    if (r >= 0.62) scored.push([r + 0.05, u, u]);
  }
  for (const [t, u] of Object.entries(cat.towns)) {
    const k = norm(t);
    let r = ratio(q, k) + (k.slice(0, 4) === q.slice(0, 4) ? 0.1 : 0) + (t.includes("міська") ? 0.08 : 0);
    if (u && norm(u).slice(0, 5) === k.slice(0, 5)) r += 0.1;
    if (r >= 0.62) scored.push([r, t, u]);
  }
  scored.sort((a, b) => b[0] - a[0]);
  const hits = [];
  for (const [, label, u] of scored) {
    const served = units.has(u) ? u : null;
    if (served && hits.some((h) => h[1] === served)) continue;
    const text = label === u ? label : `${label.replace(" територіальна громада", "")} → ${served || "район не обслуговується"}`;
    hits.push([text, served]);
    if (hits.length >= n) break;
  }
  return hits;
}
