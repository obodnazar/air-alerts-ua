"""Compute forecasts for ALL raions once and cache them; the bot only reads the cache.

evening(day): as of 20:00 the day before; P(alert at 08:00) and P(all-clear by 09/10/11/12).
morning(day): at 08:00, for raions under alert: level, minutes elapsed, P(all-clear by 09/10/11/12).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .. import models as M
from ..attacks import load_reports
from ..clearance import WINDOW, fit_predict
from ..data import build_store, eligible_units
from ..dataset import build_m1, build_m2, kyiv

CACHE = Path("results/live")
CPS = (9, 10, 11, 12)


def _world():
    store = build_store(Path("data/raw/vadimkin/official_data_uk.csv"), Path("data/interim/telegram_episodes.parquet"))
    return store, load_reports(Path("data/raw/kaggle/missile_attacks_daily.csv")), eligible_units(store)


def _recent(store, reports, keep, d, before):
    r = build_m2(store, reports, (d - pd.Timedelta(days=WINDOW)).strftime("%Y-%m-%d"),
                 (d - pd.Timedelta(days=1)).strftime("%Y-%m-%d"), all_days=True)
    return r[r.unit.isin(keep) & (r.issue_time < before)]


def evening(day: str) -> pd.DataFrame:
    store, reports, keep = _world()
    d = pd.Timestamp(day)
    T = kyiv(d - pd.Timedelta(days=1), "20:00")
    hist = build_m1(store, reports, "2025-12-15", (d - pd.Timedelta(days=2)).strftime("%Y-%m-%d"))
    hist = hist[hist.unit.isin(keep) & (hist.issue == "20:00")]
    now = build_m1(store, reports, day, day)
    now = now[(now.issue == "20:00") & now.unit.isin(keep)].reset_index(drop=True)
    p = M.predict_m1(M.fit_m1(hist), now)
    recent = _recent(store, reports, keep, d, T)
    out = pd.DataFrame(dict(unit=now.unit, p_alert_08=p.logit, usual=p.climatology))
    for cp in CPS:
        out[f"p_clear_{cp}"] = fit_predict(recent, now[["unit", "oblast"]], with_08=False, horizon=(cp - 8) * 60)
    out.attrs["data_end"] = str(store.coverage[1])
    CACHE.mkdir(parents=True, exist_ok=True)
    out.to_csv(CACHE / f"bot_evening_{day}.csv", index=False)
    return out


def morning(day: str) -> pd.DataFrame:
    store, reports, keep = _world()
    d = pd.Timestamp(day)
    recent = _recent(store, reports, keep, d, kyiv(d, "08:00"))
    now = build_m2(store, reports, day, day, all_days=True)
    now = now[now.unit.isin(keep)].reset_index(drop=True)
    out = pd.DataFrame(dict(unit=now.unit, level=now.level, elapsed_min=now.elapsed))
    for cp in CPS:
        out[f"p_clear_{cp}"] = fit_predict(recent, now, with_08=True, horizon=(cp - 8) * 60) if len(now) else []
    CACHE.mkdir(parents=True, exist_ok=True)
    out.to_csv(CACHE / f"bot_morning_{day}.csv", index=False)
    return out


def cached(kind: str, day: str) -> pd.DataFrame | None:
    f = CACHE / f"bot_{kind}_{day}.csv"
    return pd.read_csv(f) if f.exists() else None
