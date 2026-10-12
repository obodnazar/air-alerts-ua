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

/** Everything the hour screen needs. params: the /ingest "hourly" body; live: {on, elapsed} or null if the live
 *  state is unknown (stale scrape): then only the historical share is given. */
export function hourRisk(params, unit, day, hour, now, live) {
  const u = params?.units?.[unit];
  if (!u) return null;
  const x = kyivToUtc(day, hour), step = params.step || 10;
  const surv = params.surv;
  const hx = kyivHour(x), clim = u.clim[hx];
  const lead = (x - now) / MIN;
  if (lead <= 0) return { past: true, clim };
  const steps = Math.min(Math.floor(lead / step), surv.length - 1);
  const b = bucketOf(params.gates.buckets, lead / 60);
  const out = { clim, lead, bucket: b, x, model: null };
  if (!live) return out;
  if (!params.gates[live.on ? "on" : "off"][b]) return out; // here the model is not better than history
  const c = coverageShare(u.prof, surv, x, step);
  out.model = Math.min(1, Math.max(0, forecastOne(clim, c, surv, live.on, live.elapsed, steps, step)));
  return out;
}
