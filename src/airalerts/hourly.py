"""Hourly alert risk per raion, for today and tomorrow (post-hoc, PREREG item 12).

Question for the bot: "will an alert be on in my raion at 15:00?", asked at any time.
Target: alert active at a whole hour h:00 (Kyiv), leads 1-47 h.

Baseline (what the bot shows where the model does not beat it): the raion's share of the previous
28 days with an alert at h:00 ("climatology").

Model: the same climatology, adjusted for the state at the issue time T.
  - no alert at T:   P = 1 - (1 - clim)^C(L).  C(L) is the share of the alerts covering h:00 that start within
    the last L minutes before it, from the raion's start profile by hour and the duration curve S. With Poisson
    starts this is exact; as L grows, C -> 1 and P -> clim.
  - alert on at T, running e min:  P = S(L|e) + sum_tau f(tau|e) * [1 - (1 - clim)^C(L - tau)]:
    the alert is still on, or it ended at tau and a new one covers h:00.
S is the Kaplan-Meier survival of alert durations (merged chains, previous 42 days, pooled, 24 h+ censored),
conditional on the elapsed time: the curve clearance.km_rate reads at one point (120 min), here on a 10-min grid,
because the per-raion logistic in clearance.py is fitted for 08:00 only and gives no curve.

Parameters use only data before 00:00 of the issue day; the state (on/off, elapsed) is read at T.
The Worker (deploy/cloudflare/worker/src/hourly.js) runs the same formulas on the published parameters and its own
live state; test/fixtures/hourly_parity.json, written by scripts/make_worker_fixtures.py, keeps the two equal.

    PYTHONPATH=src python -m airalerts.hourly          # rolling backtest -> results/posthoc_hourly_*.csv, hourly_gates.json
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .data import KYIV_TZ, Store, build_store, eligible_units

STEP = 10                     # minutes per grid step
GRID = 48 * 60 // STEP        # 288 steps: up to 48 h ahead
CAP = 24 * 60 // STEP         # 144 steps: alerts of 24 h+ are censored (as in data.py)
CLIM_DAYS, SURV_DAYS = 28, 42
K = 20.0                      # shrinkage of a raion's start profile to its oblast, in pseudo-starts
BUCKETS = [(1, 1), (2, 2), (3, 3), (4, 6), (7, 12), (13, 24), (25, 47)]  # lead in hours, inclusive
EVAL_FROM, HALF = "2026-09-15", "2026-09-29"
GATES = Path("results/hourly_gates.json")


# ---------- time ----------

def minute(t: pd.Timestamp) -> float:
    return pd.Timestamp(t).tz_convert("UTC").value / 60_000_000_000


def kyiv_minute(day: pd.Timestamp, hour: int) -> float:
    """UTC minute of h:00 Kyiv local time on a calendar day. On the autumn DST switch 03:00 happens twice: the
    first one (summer time) is taken; a missing spring hour moves forward. The Worker's kyivToUtc does the same."""
    d = pd.Timestamp(day)
    local = pd.Timestamp(d.year, d.month, d.day, hour).tz_localize(KYIV_TZ, ambiguous=True, nonexistent="shift_forward")
    return minute(local)


def kyiv_hours(minutes: np.ndarray) -> np.ndarray:
    """Kyiv local hour of each UTC minute (DST-aware)."""
    t = pd.to_datetime(np.asarray(minutes, dtype=float) * 60, unit="s", utc=True).tz_convert(KYIV_TZ)
    return np.asarray(t.hour, dtype=int)


def bucket_of(lead_hours: float) -> int:
    """Index into BUCKETS for a lead in hours (rounded up: 40 min ahead is the 1-hour bucket)."""
    h = max(1, int(np.ceil(lead_hours - 1e-9)))
    for i, (_, hi) in enumerate(BUCKETS):
        if h <= hi:
            return i
    return len(BUCKETS) - 1


# ---------- state and parameters, as of a time ----------

def state_at(store: Store, units: list[str], minutes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """on[u, k]: alert active at minutes[k]; start[u, k]: start minute of the running chain (nan if off).
    Same rule as Store.active_until: the chain that started last at or before t, if it ends after t."""
    minutes = np.atleast_1d(np.asarray(minutes, dtype=float))
    on = np.zeros((len(units), len(minutes)), bool)
    start = np.full((len(units), len(minutes)), np.nan)
    for i, u in enumerate(units):
        s, e = store.starts[u], store.ends[u]
        if not len(s):
            continue
        k = np.searchsorted(s, minutes, side="right") - 1
        kk = np.clip(k, 0, None)
        act = (k >= 0) & (e[kk] > minutes)
        on[i] = act
        start[i] = np.where(act, s[kk], np.nan)
    return on, start


def km_grid(dur: np.ndarray, event: np.ndarray) -> np.ndarray:
    """Kaplan-Meier S(t) = P(duration > t) at t = 0, STEP, ..., GRID*STEP; zero from CAP on (24 h+ alerts are cut
    in data.py, so no forecast keeps an alert alive past a day)."""
    t = np.arange(GRID + 1) * STEP
    if not len(dur):
        return np.where(np.arange(GRID + 1) < CAP, 1.0, 0.0)
    times = np.unique(dur[event])
    if not len(times):  # no finished alert at all: nothing ends before the cap (audit 7: this raised IndexError)
        return np.where(np.arange(GRID + 1) < CAP, 1.0, 0.0)
    surv, s = [], 1.0
    for x in times:
        at_risk = (dur >= x).sum()
        s *= 1 - ((dur == x) & event).sum() / at_risk
        surv.append(s)
    surv = np.array(surv)
    idx = np.searchsorted(times, t, side="right") - 1
    out = np.where(idx >= 0, surv[np.clip(idx, 0, None)], 1.0)
    out[np.arange(GRID + 1) >= CAP] = 0.0
    return out


def params_asof(store: Store, units: list[str], day: pd.Timestamp) -> dict:
    """Everything the model needs except the state, from data strictly before 00:00 Kyiv of `day`."""
    day = pd.Timestamp(day).normalize()
    cut = kyiv_minute(day, 0)
    days = [day - pd.Timedelta(int(k), "D") for k in range(CLIM_DAYS, 0, -1)]
    # climatology: share of the previous 28 days with an alert at h:00
    clim = np.zeros((len(units), 24))
    for h in range(24):
        on, _ = state_at(store, units, np.array([kyiv_minute(d, h) for d in days]))
        clim[:, h] = on.mean(axis=1)
    # start profile by local hour, previous 28 days; raion shrunk to its oblast, oblast smoothed by +1 per hour
    lo = kyiv_minute(days[0], 0)
    counts = np.zeros((len(units), 24))
    for i, u in enumerate(units):
        s = store.starts[u]
        s = s[(s >= lo) & (s < cut)]
        if len(s):
            counts[i] = np.bincount(kyiv_hours(s), minlength=24)
    oblast = np.array([store.oblast_of[u] for u in units])
    prof = np.zeros_like(counts)
    for ob in np.unique(oblast):
        m = oblast == ob
        p_ob = (counts[m].sum(axis=0) + 1) / (counts[m].sum() + 24)
        prof[m] = (counts[m] + K * p_ob) / (counts[m].sum(axis=1, keepdims=True) + K)
    # durations of chains that started in the previous 42 days; still running at 00:00 -> censored there
    lo42 = cut - SURV_DAYS * 24 * 60
    dur, ev = [], []
    for u in units:
        s, e = store.starts[u], store.ends[u]
        m = (s >= lo42) & (s < cut)
        d = np.minimum(e[m], cut) - s[m]
        done = e[m] <= cut
        dur.append(d)
        ev.append(done & (d < CAP * STEP))
    dur, ev = np.concatenate(dur), np.concatenate(ev)
    return dict(day=day, clim=clim, prof=prof, surv=km_grid(np.minimum(dur, CAP * STEP), ev))


# ---------- the forecast ----------

def coverage_share(prof_u: np.ndarray, surv: np.ndarray, x_min: float) -> np.ndarray:
    """C[j] for j = 0..GRID: share of the alerts covering x that started within j steps before it.
    prof_u: (n, 24) start profiles; returns (n, GRID+1)."""
    j = np.arange(1, GRID + 1)
    w = np.atleast_2d(prof_u)[:, kyiv_hours(x_min - STEP * j)] * surv[j]
    a = np.concatenate([np.zeros((w.shape[0], 1)), np.cumsum(w, axis=1)], axis=1)
    return a / np.where(a[:, -1:] > 0, a[:, -1:], 1)


def p_new(clim: np.ndarray, c: np.ndarray) -> np.ndarray:
    """P(a new alert, started after the issue time, covers x)."""
    return 1 - np.power(1 - clim, c)


def forecast_one(clim: float, c_row: np.ndarray, surv: np.ndarray, on: bool, elapsed: float, steps: int) -> float:
    """Scalar version, the reference the Worker is checked against. steps = lead in STEP units."""
    if not on:
        return float(p_new(clim, c_row[steps]))
    k = int(elapsed // STEP)
    if k >= len(surv) or surv[k] <= 0:
        return 1.0  # running past the 24 h cap: a stuck alert, assume it stays
    s = np.array([surv[k + t] if k + t < len(surv) else 0.0 for t in range(steps + 1)]) / surv[k]
    f = s[:-1] - s[1:]  # ended in step tau = 1..steps
    tail = p_new(clim, c_row[steps - np.arange(1, steps + 1)])
    return float(s[-1] + (f * tail).sum())


def forecast(par: dict, idx: np.ndarray, on: np.ndarray, elapsed: np.ndarray, x_min: float, steps: int,
             c: np.ndarray | None = None) -> np.ndarray:
    """Vectorised over units idx (all with the same target x and lead `steps`)."""
    hx = int(kyiv_hours(np.array([x_min]))[0])
    clim = par["clim"][idx, hx]
    if c is None:
        c = coverage_share(par["prof"][idx], par["surv"], x_min)
    out = p_new(clim, c[:, steps])
    if on.any():
        surv = np.concatenate([par["surv"], np.zeros(GRID + 1)])
        k = (elapsed[on] // STEP).astype(int)
        base = surv[np.clip(k, 0, len(surv) - 1)]
        stuck = (k >= len(par["surv"])) | (base <= 0)
        t = np.arange(steps + 1)
        kk = np.clip(k[:, None] + t[None, :], 0, len(surv) - 1)
        s = surv[kk] / np.where(base > 0, base, 1)[:, None]
        f = s[:, :-1] - s[:, 1:]
        tail = p_new(clim[on][:, None], c[on][:, steps - np.arange(1, steps + 1)])
        p_on = s[:, -1] + (f * tail).sum(axis=1)
        out[on] = np.where(stuck, 1.0, p_on)
    return out


# ---------- backtest ----------

def backtest(store: Store, units: list[str], start: str = EVAL_FROM) -> pd.DataFrame:
    """Rolling: for each day d, parameters as of d 00:00; issued at every whole hour of d; targets every whole hour
    after the issue up to 23:00 of d+1 that the data cover. Returns per (day, state, bucket) sums of squared errors."""
    end_min = minute(store.coverage[1])
    last = (store.coverage[1].tz_convert(KYIV_TZ) - pd.Timedelta(hours=1)).normalize().tz_localize(None)
    recs = []
    n_units = len(units)
    for d in pd.date_range(start, last, freq="D"):
        par = params_asof(store, units, d)
        targets = [(dd, h) for dd in (d, d + pd.Timedelta(1, "D")) for h in range(24)]
        tmin = np.array([kyiv_minute(dd, h) for dd, h in targets])
        keep = tmin <= end_min
        tmin, targets = tmin[keep], [t for t, k in zip(targets, keep) if k]
        y_all, _ = state_at(store, units, tmin)
        c_all = {k: coverage_share(par["prof"], par["surv"], x) for k, x in enumerate(tmin)}
        hx_all = kyiv_hours(tmin)
        all_idx = np.arange(n_units)
        for h0 in range(24):
            T = kyiv_minute(d, h0)
            if T >= end_min:
                break
            on, st = state_at(store, units, np.array([T]))
            on, el = on[:, 0], T - st[:, 0]
            for k, x in enumerate(tmin):
                if x <= T:
                    continue
                lead_h = (x - T) / 60
                steps = int(round((x - T) / STEP))
                if steps > GRID:
                    continue
                p = forecast(par, all_idx, on, el, x, steps, c=c_all[k])
                clim = par["clim"][:, hx_all[k]]
                y = y_all[:, k].astype(float)
                b = bucket_of(lead_h)
                for state in (True, False):
                    m = on == state
                    if not m.any():
                        continue
                    recs.append((d, "on" if state else "off", b, int(m.sum()), y[m].sum(),
                                 ((p[m] - y[m]) ** 2).sum(), ((clim[m] - y[m]) ** 2).sum(), p[m].sum(), clim[m].sum()))
    cols = ["day", "state", "bucket", "n", "y", "se_model", "se_clim", "p_model", "p_clim"]
    df = pd.DataFrame(recs, columns=cols)
    return df.groupby(["day", "state", "bucket"], as_index=False).sum()


def bootstrap_sums(daily: pd.DataFrame, block: int = 3, n: int = 1000, seed: int = 0) -> tuple[float, float, float]:
    """models.block_bootstrap_diff on per-day sums: the same resampling of 3-day blocks (same seed), so the same
    numbers, without holding 2 million rows. BSS = 1 - Brier(model) / Brier(climatology)."""
    g = daily.groupby("day")[["se_model", "se_clim"]].sum().sort_index()
    a, b = g.se_model.to_numpy(), g.se_clim.to_numpy()
    blocks = [np.arange(i, min(i + block, len(g))) for i in range(0, len(g), block)]
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        pick = np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))])
        out.append(1 - a[pick].sum() / b[pick].sum())
    return float(1 - a.sum() / b.sum()), float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def summarise(daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (state, b), g in daily.groupby(["state", "bucket"]):
        est, lo, hi = bootstrap_sums(g)
        first, second = g[g.day < HALF], g[g.day >= HALF]
        half = [1 - h.se_model.sum() / h.se_clim.sum() if len(h) else np.nan for h in (first, second)]
        n = g.n.sum()
        rows.append(dict(state=state, lead=f"{BUCKETS[b][0]}-{BUCKETS[b][1]} h" if BUCKETS[b][0] != BUCKETS[b][1]
                         else f"{BUCKETS[b][0]} h", bucket=b, n=int(n), days=g.day.nunique(), alert_rate=g.y.sum() / n,
                         mean_p_model=g.p_model.sum() / n, mean_clim=g.p_clim.sum() / n,
                         brier_model=g.se_model.sum() / n, brier_clim=g.se_clim.sum() / n,
                         bss=est, ci_lo=lo, ci_hi=hi, bss_first_half=half[0], bss_second_half=half[1],
                         show_model=bool(lo > 0 and half[0] > 0 and half[1] > 0)))
    return pd.DataFrame(rows).sort_values(["state", "bucket"], ascending=[False, True]).reset_index(drop=True)


def gates(summary: pd.DataFrame) -> dict:
    """The display rule registered in PREREG item 12, as the Worker reads it."""
    out = {"buckets": [list(b) for b in BUCKETS]}
    for state in ("on", "off"):
        s = summary[summary.state == state].set_index("bucket").show_model
        out[state] = [bool(s.get(i, False)) for i in range(len(BUCKETS))]
    return out


# ---------- version 2 (PREREG item 13): a stacked logistic with wave features ----------

TRAIN_DAYS, MAX_TRAIN, P_CLIP = 14, 80_000, 0.002
FEATURES = ["l_h1", "l_clim", "occ6", "occ24", "oblast_on", "country_on"]   # + "l_elapsed" when an alert is on
GATES2 = Path("results/hourly_gates_v2.json")
COEF = Path("results/hourly_coef_v2.json")


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), P_CLIP, 1 - P_CLIP)
    return np.log(p / (1 - p))


def occupancy(store: Store, units: list[str], t: float, hours: int) -> np.ndarray:
    """Share of the `hours` before t each unit spent under alert. Only chains that started before t, cut at t: what
    was known at t. Chains starting up to 7 days earlier are scanned: merged chains can exceed 24 h (audit 7)."""
    lo, out = t - hours * 60, np.zeros(len(units))
    for i, u in enumerate(units):
        s, e = store.starts[u], store.ends[u]
        a, b = np.searchsorted(s, lo - 7 * 24 * 60), np.searchsorted(s, t)
        if b > a:
            out[i] = np.clip(np.minimum(e[a:b], t) - np.maximum(s[a:b], lo), 0, None).sum() / (hours * 60)
    return np.clip(out, 0, 1)


def wave_features(store: Store, units: list[str], t: float, on: np.ndarray) -> dict:
    """occ6, occ24, oblast_on, country_on for every unit at time t (on: state at t, from state_at)."""
    ob = np.array([store.oblast_of[u] for u in units])
    oblast_on = np.zeros(len(units))
    for o in np.unique(ob):
        m = ob == o
        oblast_on[m] = on[m].mean()
    return dict(occ6=occupancy(store, units, t, 6), occ24=occupancy(store, units, t, 24),
                oblast_on=oblast_on, country_on=np.full(len(units), on.mean()))


def design(p_h1, clim, feats: dict, elapsed, on_state: bool) -> np.ndarray:
    cols = [logit(p_h1), logit(clim), feats["occ6"], feats["occ24"], feats["oblast_on"], feats["country_on"]]
    if on_state:
        cols.append(np.log1p(np.clip(elapsed, 0, None)))
    return np.column_stack(cols)


def day_pairs(store: Store, units: list[str], d: pd.Timestamp, end_min: float, every: int = 1) -> pd.DataFrame:
    """Every (issue hour of day d, unit, target hour) pair the data can score: H1, history, wave features, outcome."""
    par = params_asof(store, units, d)
    targets = [(dd, h) for dd in (d, d + pd.Timedelta(1, "D")) for h in range(24)]
    tmin = np.array([kyiv_minute(dd, h) for dd, h in targets])
    tmin = tmin[tmin <= end_min]
    y_all, _ = state_at(store, units, tmin)
    c_all = {k: coverage_share(par["prof"], par["surv"], x) for k, x in enumerate(tmin)}
    hx_all = kyiv_hours(tmin)
    idx, out = np.arange(len(units)), []
    for h0 in range(0, 24, every):
        T = kyiv_minute(d, h0)
        if T >= end_min:
            break
        on, st = state_at(store, units, np.array([T]))
        on, el = on[:, 0], T - st[:, 0]
        f = wave_features(store, units, T, on)
        for k, x in enumerate(tmin):
            if x <= T:
                continue
            steps = int(round((x - T) / STEP))
            if steps > GRID:
                continue
            p1 = forecast(par, idx, on, el, x, steps, c=c_all[k])
            out.append(pd.DataFrame(dict(day=d, T=T, x=x, unit=idx, on=on, bucket=bucket_of((x - T) / 60),
                                         y=y_all[:, k].astype(np.float32), p1=p1.astype(np.float32),
                                         clim=par["clim"][:, hx_all[k]].astype(np.float32),
                                         elapsed=np.where(on, el, 0).astype(np.float32),
                                         **{n: v.astype(np.float32) for n, v in f.items()})))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def training_frame(pairs: dict, d: pd.Timestamp, cover_end: float) -> pd.DataFrame:
    """The rows day d may learn from: issued in the 14 days before d, target hour before 00:00 of d AND inside the
    data actually seen (an unseen hour would be labelled "no alert"; audit 7). One function for the backtest and the
    publisher, so both fit the same model."""
    cut = min(kyiv_minute(d, 0), cover_end)
    hist = pd.concat([pairs[d - pd.Timedelta(k, "D")] for k in range(TRAIN_DAYS, 0, -1)
                      if (d - pd.Timedelta(k, "D")) in pairs], ignore_index=True)
    return hist[(hist.x < cut) & (hist.day < d)]


def fit_v2(train: pd.DataFrame, seed: int = 0) -> dict:
    """Coefficients per (state, bucket) from pairs whose outcome is known. Missing or one-class cells -> None
    (the caller falls back to H1)."""
    from sklearn.linear_model import LogisticRegression

    coef = {"on": [None] * len(BUCKETS), "off": [None] * len(BUCKETS)}
    for (on_state, b), g in train.groupby(["on", "bucket"]):
        if len(g) > MAX_TRAIN:
            g = g.sample(MAX_TRAIN, random_state=seed)
        if len(g) < 200 or g.y.nunique() < 2:
            continue
        X = design(g.p1, g.clim, g, g.elapsed, bool(on_state))
        m = LogisticRegression(C=1.0, max_iter=500).fit(X, g.y.astype(int))
        coef["on" if on_state else "off"][int(b)] = [float(m.intercept_[0]), *map(float, m.coef_[0])]
    return coef


def predict_v2(coef: dict, rows: pd.DataFrame) -> np.ndarray:
    p = rows.p1.to_numpy(dtype=float).copy()
    for (on_state, b), g in rows.groupby(["on", "bucket"]):
        c = coef["on" if on_state else "off"][int(b)]
        if c is None:
            continue
        X = design(g.p1, g.clim, g, g.elapsed, bool(on_state))
        p[g.index.to_numpy()] = 1 / (1 + np.exp(-(c[0] + X @ np.array(c[1:]))))
    return p


def backtest_v2(store: Store, units: list[str], start: str = EVAL_FROM) -> pd.DataFrame:
    """Rolling: day d is predicted with coefficients fitted on the 14 days before it, outcomes known by d 00:00."""
    end_min = minute(store.coverage[1])
    last = (store.coverage[1].tz_convert(KYIV_TZ) - pd.Timedelta(hours=1)).normalize().tz_localize(None)
    first = pd.Timestamp(start) - pd.Timedelta(TRAIN_DAYS, "D")
    pairs = {d: day_pairs(store, units, d, end_min) for d in pd.date_range(first, last, freq="D")}
    recs = []
    for d in pd.date_range(start, last, freq="D"):
        coef = fit_v2(training_frame(pairs, d, end_min), seed=int(d.strftime("%Y%m%d")))
        rows = pairs[d].reset_index(drop=True)
        p2 = predict_v2(coef, rows)
        y, p1, cl = rows.y.to_numpy(float), rows.p1.to_numpy(float), rows.clim.to_numpy(float)
        g = pd.DataFrame(dict(state=np.where(rows.on, "on", "off"), bucket=rows.bucket, n=1, y=y,
                              se_model=(p2 - y) ** 2, se_h1=(p1 - y) ** 2, se_clim=(cl - y) ** 2,
                              p_model=p2, p_h1=p1, p_clim=cl))
        recs.append(g.groupby(["state", "bucket"], as_index=False).sum().assign(day=d))
    return pd.concat(recs, ignore_index=True)


def coef_asof(store: Store, units: list[str], day: pd.Timestamp, every: int = 1) -> dict | None:
    """What the publisher sends: coefficients fitted exactly as in the backtest (every issue hour; audit 7 found that
    sampling every 3 h fitted a different model). None if the data end more than 36 h before `day`: a stale fit is
    not published, and the Worker then falls back to version 1 or history."""
    day = pd.Timestamp(day).normalize()
    cover_end = minute(store.coverage[1])
    if cover_end < kyiv_minute(day, 0) - 36 * 60:
        return None
    pairs = {day - pd.Timedelta(k, "D"): day_pairs(store, units, day - pd.Timedelta(k, "D"), min(cover_end, kyiv_minute(day, 0)), every)
             for k in range(TRAIN_DAYS, 0, -1)}
    return fit_v2(training_frame(pairs, day, cover_end), seed=int(day.strftime("%Y%m%d")))


SNAPSHOT_V2 = Path("data/snapshot/telegram_episodes_2026-10-13.parquet")   # the exact data of the PREREG 13 run
MANIFEST_V2 = Path("results/hourly_v2_manifest.json")


def main_v2(episodes: Path = Path("data/interim/telegram_episodes.parquet"), check: bool = False) -> None:
    """--v2: run the backtest and write results. --v2 --check: rerun on the committed snapshot and compare with the
    committed summary and gates (scripts/reproduce.sh; audit 7 found the committed numbers were not reproducible)."""
    import hashlib

    store = build_store(Path("data/raw/vadimkin/official_data_uk.csv"), episodes)
    units = eligible_units(store)
    daily = backtest_v2(store, units)
    summary = summarise(daily)
    vs_h1 = summarise(daily.assign(se_clim=daily.se_h1))[["state", "bucket", "bss", "ci_lo", "ci_hi"]]
    summary = summary.merge(vs_h1.rename(columns={"bss": "bss_vs_h1", "ci_lo": "vs_h1_lo", "ci_hi": "vs_h1_hi"}),
                            on=["state", "bucket"])
    h1 = daily.groupby(["state", "bucket"]).apply(lambda g: g.p_h1.sum() / g.n.sum(), include_groups=False)
    summary["mean_p_h1"] = [h1[(s, b)] for s, b in zip(summary.state, summary.bucket)]
    if check:
        ref = pd.read_csv("results/posthoc_hourly_v2_summary.csv")
        cols = ["n", "bss", "ci_lo", "ci_hi", "bss_first_half", "bss_second_half", "bss_vs_h1"]
        bad = [c for c in cols if not np.allclose(summary[cols].to_numpy(float), ref[cols].to_numpy(float), atol=1e-9)]
        same_gates = gates(summary) == json.loads(GATES2.read_text(encoding="utf-8"))
        if bad or not same_gates:
            raise SystemExit(f"hourly v2: results differ from the committed ones ({bad}, gates equal: {same_gates})")
        print("hourly v2: results match the committed ones")
        return
    daily.to_csv("results/posthoc_hourly_v2_daily.csv", index=False)
    summary.to_csv("results/posthoc_hourly_v2_summary.csv", index=False)
    GATES2.write_text(json.dumps(gates(summary), indent=1) + "\n", encoding="utf-8")
    MANIFEST_V2.write_text(json.dumps(dict(episodes=str(episodes), sha256=hashlib.sha256(episodes.read_bytes()).hexdigest(),
                                           coverage_end=str(store.coverage[1]), units=len(units),
                                           forecasts=int(daily.n.sum())), indent=1) + "\n", encoding="utf-8")
    print(f"hourly risk v2 (PREREG 13), rolling {EVAL_FROM} → {daily.day.max():%Y-%m-%d}, {len(units)} units")
    print(summary[["state", "lead", "n", "alert_rate", "mean_p_h1", "mean_p_model", "bss", "ci_lo", "ci_hi",
                   "bss_first_half", "bss_second_half", "bss_vs_h1", "show_model"]].round(4).to_string(index=False))


# ---------- what the publisher sends ----------

def payload(store: Store, units: list[str], day: pd.Timestamp, gate: dict | None = None, v2: bool = True) -> dict:
    """Per-raion parameters for the Worker: climatology and start profile by hour, the duration curve, the gates;
    version 2 (PREREG 13): each raion's oblast, the logistic coefficients fitted on the 14 days before `day`, its gates."""
    par = params_asof(store, units, day)
    gate = gate or json.loads(GATES.read_text(encoding="utf-8"))
    prof = par["prof"] / par["prof"].sum(axis=1, keepdims=True)
    out = dict(as_of=pd.Timestamp(day).strftime("%Y-%m-%d"), step=STEP,
               units={u: dict(clim=[round(float(x), 4) for x in par["clim"][i]],
                              prof=[round(float(x), 5) for x in prof[i]], ob=store.oblast_of[u]) for i, u in enumerate(units)},
               surv=[round(float(x), 5) for x in par["surv"]], gates=gate)
    coef = coef_asof(store, units, day) if v2 and GATES2.exists() else None
    if coef is not None:
        out["coef"] = {k: [None if c is None else [round(x, 6) for x in c] for c in v] for k, v in coef.items()}
        out["gates2"] = json.loads(GATES2.read_text(encoding="utf-8"))
    return out


def main() -> None:
    store = build_store(Path("data/raw/vadimkin/official_data_uk.csv"), Path("data/interim/telegram_episodes.parquet"))
    units = eligible_units(store)
    daily = backtest(store, units)
    daily.to_csv("results/posthoc_hourly_daily.csv", index=False)
    summary = summarise(daily)
    summary.to_csv("results/posthoc_hourly_summary.csv", index=False)
    GATES.write_text(json.dumps(gates(summary), indent=1) + "\n", encoding="utf-8")
    print(f"hourly risk, rolling {EVAL_FROM} → {daily.day.max():%Y-%m-%d}, {len(units)} units, issued every hour")
    print(summary.drop(columns=["bucket"]).round(4).to_string(index=False))
    all_ = summarise(daily.assign(state="all"))
    print("\nboth states pooled:")
    print(all_[["lead", "n", "brier_model", "brier_clim", "bss", "ci_lo", "ci_hi"]].round(4).to_string(index=False))
    wk = daily.assign(state=np.where(pd.to_datetime(daily.day).dt.weekday < 5, "weekday", "weekend"))
    print("\nweekdays vs weekends (all leads):")
    for s, g in wk.groupby("state"):
        est, lo, hi = bootstrap_sums(g)
        print(f"  {s}: BSS {est:+.4f} [{lo:+.4f}, {hi:+.4f}]  n={int(g.n.sum())}")


if __name__ == "__main__":
    import sys

    if "--v2" in sys.argv:
        ep = Path(sys.argv[sys.argv.index("--episodes") + 1]) if "--episodes" in sys.argv else Path("data/interim/telegram_episodes.parquet")
        main_v2(ep, check="--check" in sys.argv)
    else:
        main()
