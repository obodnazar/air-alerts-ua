"""All-clear by the control point, per raion, refitted every day on the last 6 weeks (post-hoc fix).

Why: the registered M2 was trained before the yellow/red reform (Sep 2026) and the
evening brief used one per-oblast curve, so all raions of an oblast got the same
number (on 12 Oct all five Kharkiv raions got 61% and none cleared by 10:00).

What: for an alert active at 08:00, P(all-clear by 10:00) from a logistic model
refitted each day on the previous WINDOW days only. Raion and oblast enter as
shrunk historical clear rates (a raion with few alerts borrows from its oblast,
an oblast from the whole country), so every raion gets its own number.
  - "20:00" version: what is known the evening before (no level, no elapsed time).
  - "08:00" version: adds the alert level in force at 08:00 and how long it has run.

Evaluated by rolling forecasts on days the model had not seen; this was added
after the registered test and is labelled as such in PREREG.md and the README.

    PYTHONPATH=src python -m airalerts.clearance
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from lifelines import KaplanMeierFitter
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import models as M
from .attacks import load_reports
from .data import build_store, eligible_units
from .dataset import build_m2

WINDOW = 42
K = 10.0  # shrinkage strength, in "pseudo-alerts"
EVAL_FROM, EVAL_TO = "2026-09-15", "2026-10-12"


def _rates(hist: pd.DataFrame) -> tuple[float, dict, dict]:
    y = hist.clear_by_10.astype(float)
    p0 = float(y.mean()) if len(y) else 0.6
    ob = hist.assign(y=y).groupby("oblast").y.agg(["sum", "count"])
    p_ob = ((ob["sum"] + K * p0) / (ob["count"] + K)).to_dict()
    ra = hist.assign(y=y).groupby(["unit", "oblast"]).y.agg(["sum", "count"]).reset_index()
    p_ra = {r.unit: (r["sum"] + K * p_ob[r.oblast]) / (r["count"] + K) for _, r in ra.iterrows()}
    return p0, p_ob, p_ra


def _features(df: pd.DataFrame, rates, with_08: bool) -> pd.DataFrame:
    p0, p_ob, p_ra = rates
    ob = df.oblast.map(p_ob).fillna(p0).clip(0.01, 0.99)
    ra = df.unit.map(p_ra).fillna(ob).clip(0.01, 0.99)
    X = pd.DataFrame(dict(raion_rate=np.log(ra / (1 - ra)), oblast_rate=np.log(ob / (1 - ob))), index=df.index)
    if with_08:
        X["red"] = (df.level == "red").astype(float)
        X["yellow"] = (df.level == "yellow").astype(float)
        X["log_elapsed"] = np.log1p(df.elapsed)
        X["country_share_now"] = df.country_share_now
        X["night_min"] = df.night_min
    return X


def fit_predict(hist: pd.DataFrame, target: pd.DataFrame, with_08: bool, horizon: float = 120) -> np.ndarray:
    """P(all-clear within `horizon` minutes after 08:00); 120 = control point 10:00."""
    known = hist.event | hist.capped | (hist.remaining > horizon)
    hist = hist[known].assign(clear_by_10=lambda x: x.event & (x.remaining <= horizon))
    rates = _rates(hist)
    # rates are computed on the same window they are fitted on; to avoid the model trusting
    # in-sample rates too much, the raion/oblast columns for fitting come from a leave-one-day-out pass
    Xh = []
    for d, g in hist.groupby("day"):
        Xh.append(_features(g, _rates(hist[hist.day != d]), with_08))
    Xh = pd.concat(Xh).loc[hist.index]
    model = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=2000))
    model.fit(Xh.to_numpy(), hist.clear_by_10.astype(int).to_numpy())
    return model.predict_proba(_features(target, rates, with_08).to_numpy())[:, 1]


def km_rate(hist: pd.DataFrame) -> float:
    return float(1 - KaplanMeierFitter().fit(hist.remaining.clip(lower=0.1), hist.event).predict(120))


def rolling_eval(rows: pd.DataFrame) -> pd.DataFrame:
    out = []
    days = sorted(rows[(rows.day >= EVAL_FROM) & (rows.day <= EVAL_TO) & (rows.day.dt.weekday < 5)].day.unique())
    for d in days:
        hist = rows[(rows.day < d) & (rows.day >= d - pd.Timedelta(days=WINDOW))]
        today = rows[(rows.day == d) & rows.clear_by_10.notna()]
        if today.empty or len(hist) < 100:
            continue
        ob_km = {o: km_rate(g) for o, g in hist.groupby("oblast") if len(g) >= 15}
        pooled = km_rate(hist)
        out.append(today.assign(
            y=today.clear_by_10.astype(float),
            km_pooled=pooled,
            km_oblast=today.oblast.map(ob_km).fillna(pooled),
            raion_20=fit_predict(hist, today, with_08=False),
            raion_08=fit_predict(hist, today, with_08=True),
        ))
    return pd.concat(out)


def main() -> None:
    store = build_store(Path("data/raw/vadimkin/official_data_uk.csv"), Path("data/interim/telegram_episodes.parquet"))
    reports = load_reports(Path("data/raw/kaggle/missile_attacks_daily.csv"))
    units = eligible_units(store)
    last = (store.coverage[1] - pd.Timedelta(hours=10)).strftime("%Y-%m-%d")
    rows = build_m2(store, reports, "2026-07-20", last, all_days=True)
    rows = rows[rows.unit.isin(units)].reset_index(drop=True)
    ev = rolling_eval(rows)
    ev.to_csv("results/posthoc_clearance_rolling.csv", index=False)
    res = []
    for c in ("km_pooled", "km_oblast", "raion_20", "raion_08"):
        est, lo, hi = M.block_bootstrap_diff(ev.y, ev[c], ev.km_oblast, ev.day)
        res.append(dict(model=c, n=len(ev), brier=((ev[c] - ev.y) ** 2).mean(), bss_vs_km_oblast=est, ci_lo=lo, ci_hi=hi))
    res = pd.DataFrame(res)
    res.to_csv("results/posthoc_clearance_summary.csv", index=False)
    print(f"rolling all-clear forecasts, weekdays {EVAL_FROM} → {EVAL_TO}, n={len(ev)}")
    print(res.round(4).to_string(index=False))
    kh = ev[ev.oblast == "Харківська область"]
    print("\nKharkiv oblast only: mean y", round(kh.y.mean(), 3), {c: round(((kh[c] - kh.y) ** 2).mean(), 4) for c in ("km_oblast", "raion_20", "raion_08")})


if __name__ == "__main__":
    main()
