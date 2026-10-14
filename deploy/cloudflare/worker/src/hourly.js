// P(alert on at a whole hour, today or tomorrow) per raion. The same formulas as src/airalerts/hourly.py
// (forecast_one, coverage_share); parameters come from /ingest (kind "hourly"), the state from the live scrape.
// test/fixtures/hourly_parity.json holds Python's numbers for 291 cases, incl. the 25 Oct DST switch.

const MIN = 60000;

/** Kyiv UTC offset in minutes: EU rule, summer time from the last Sunday of March to the last Sunday of October,
 *  01:00 UTC both ways (no Intl call: this runs hundreds of times per forecast). */
export function kyivOffset(ms) {
  const y = new Date(ms).getUTCFullYear();
  const lastSunday = (m) => { const d = new Date(Date.UTC(y, m + 1, 0)); return Date.UTC(y, m, d.getUTCDate() - d.getUTCDay(), 1); };
  return ms >= lastSunday(2) && ms < lastSunday(9) ? 180 : 120;
}
export const kyivHour = (ms) => new Date(ms + kyivOffset(ms) * MIN).getUTCHours();

/** UTC ms of h:00 Kyiv on a calendar day. The repeated autumn hour takes summer time; a missing spring hour
 *  moves forward (as hourly.kyiv_minute in Python). */
export function kyivToUtc(day, hour) {
  const [y, m, d] = day.split("-").map(Number);
  const local = Date.UTC(y, m - 1, d, hour);
  const summer = local - 180 * MIN;
  return kyivOffset(summer) === 180 ? summer : local - 120 * MIN;
}

/** C[j], j = 0..grid: share of the alerts covering x that started within j steps before it. */
export function coverageShare(prof, surv, x, step) {
  const grid = surv.length - 1, a = new Float64Array(grid + 1);
  for (let j = 1; j <= grid; j++) a[j] = a[j - 1] + prof[kyivHour(x - j * step * MIN)] * surv[j];
  const total = a[grid] > 0 ? a[grid] : 1;
  for (let j = 0; j <= grid; j++) a[j] /= total;
  return a;
}

const pNew = (clim, c) => 1 - Math.pow(1 - clim, c);

/** hourly.forecast_one: steps = lead in grid steps; elapsed in minutes. */
export function forecastOne(clim, c, surv, on, elapsed, steps, step = 10) {
  if (!on) return pNew(clim, c[steps]);
  const k = Math.floor(elapsed / step);
  if (k >= surv.length || surv[k] <= 0) return 1;
  const s = (t) => (k + t < surv.length ? surv[k + t] : 0) / surv[k];
  let p = 0, prev = s(0);
  for (let tau = 1; tau <= steps; tau++) { const cur = s(tau); p += (prev - cur) * pNew(clim, c[steps - tau]); prev = cur; }
  return s(steps) + p;
}

/** Index into the published lead buckets (lead rounded up to whole hours; 40 min ahead -> the 1-hour bucket). */
export function bucketOf(buckets, leadHours) {
  const h = Math.max(1, Math.ceil(leadHours - 1e-9));
  const i = buckets.findIndex(([, hi]) => h <= hi);
  return i < 0 ? buckets.length - 1 : i;
}

// ---------- version 2 (PREREG 13): logistic on top of H1 with wave features (hourly.design / predict_v2) ----------
const P_CLIP = 0.002;
export const logit = (p) => { const q = Math.min(1 - P_CLIP, Math.max(P_CLIP, p)); return Math.log(q / (1 - q)); };

/** Share of the `hours` before now the raion spent under alert; rows: its alerts {started, ended|null} (ms). The
 *  union of intervals, cut at now (Python measures merged chains; overlaps are merged here the same way). */
export function occupancy(rows, now, hours) {
  const lo = now - hours * 3600000;
  const iv = rows.filter((r) => r.started < now).map((r) => [Math.max(r.started, lo), Math.min(r.ended ?? now, now)])
    .filter(([a, b]) => b > a).sort((p, q) => p[0] - q[0]);
  let total = 0, curA = null, curB = null;
  for (const [a, b] of iv) {
    if (curB === null || a > curB) { if (curB !== null) total += curB - curA; curA = a; curB = b; } else curB = Math.max(curB, b);
  }
  if (curB !== null) total += curB - curA;
  return Math.min(1, total / (hours * 3600000));
}

/** occ6, occ24, oblast_on, country_on at now. onUnits: Set of raions with an alert on now (live scrape). */
export function waveFeatures(params, unit, unitRows, onUnits, now) {
  const names = Object.keys(params.units), ob = params.units[unit].ob;
  const same = names.filter((u) => params.units[u].ob === ob);
  const share = (list) => (list.length ? list.filter((u) => onUnits.has(u)).length / list.length : 0);
  return { occ6: occupancy(unitRows, now, 6), occ24: occupancy(unitRows, now, 24), oblast_on: share(same), country_on: share(names) };
}

/** predict_v2 for one forecast. coef = [a, b_h1, b_clim, b_occ6, b_occ24, b_oblast, b_country(, b_elapsed)]. */
export function forecastV2(coef, p1, clim, f, on, elapsed) {
  const x = [logit(p1), logit(clim), f.occ6, f.occ24, f.oblast_on, f.country_on, ...(on ? [Math.log1p(Math.max(0, elapsed))] : [])];
  let z = coef[0];
  for (let i = 0; i < x.length; i++) z += coef[i + 1] * x[i];
  return 1 / (1 + Math.exp(-z));
}

/** Everything the hour screen needs. params: the /ingest "hourly" body; live: {on, elapsed} or null if the live
 *  state is unknown (stale scrape): then only the historical share is given. feats: waveFeatures() or null.
 *  Shown: version 2 where its gate is open, else version 1 where its gate is open, else history. */
export function hourRisk(params, unit, day, hour, now, live, feats = null) {
  const u = params?.units?.[unit];
  if (!u) return null;
  const x = kyivToUtc(day, hour), step = params.step || 10;
  const surv = params.surv;
  const hx = kyivHour(x), clim = u.clim[hx];
  const lead = (x - now) / MIN;
  if (lead <= 0) return { past: true, clim, asOf: params.as_of };
  const steps = Math.min(Math.floor(lead / step), surv.length - 1);
  const b = bucketOf(params.gates.buckets, lead / 60);
  // reason: why history is shown instead of a model, so the screen can say it truthfully (audit 7)
  const out = { clim, lead, bucket: b, x, model: null, version: null, asOf: params.as_of, reason: null };
  const today = new Date(now + kyivOffset(now) * MIN).toISOString().slice(0, 10);
  const yesterday = new Date(Date.parse(`${today}T12:00:00Z`) - 86400000).toISOString().slice(0, 10);
  if (!live) { out.reason = "stale_live"; return out; }
  if (lead < 60) { out.reason = "short"; return out; }               // backtested from 1 h on: never extrapolated to minutes
  if (!params.as_of || params.as_of < yesterday) { out.reason = "stale_params"; return out; }
  const state = live.on ? "on" : "off";
  const c = coverageShare(u.prof, surv, x, step);
  const p1 = Math.min(1, Math.max(0, forecastOne(clim, c, surv, live.on, live.elapsed, steps, step)));
  const coef = params.coef?.[state]?.[b];
  if (feats && coef && params.gates2?.[state]?.[b]) { out.model = forecastV2(coef, p1, clim, feats, live.on, live.elapsed); out.version = 2; }
  else if (params.gates[state][b]) { out.model = p1; out.version = 1; }
  else out.reason = params.gates2?.[state]?.[b] && !coef ? "no_model" : "gate";
  return out;
}
