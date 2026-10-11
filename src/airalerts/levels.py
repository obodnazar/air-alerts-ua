"""If an alert is on at 08:00: chance of the all-clear by 08:30, 09:00, 10:00, 11:00, 12:00,
by alert level (yellow / red, introduced 4–6 Sep 2026) and by how long it has already lasted.

Two parts:
1. Descriptive table on all days since levels exist (Kaplan–Meier, so alerts still running
   at the end of the data count as censored, not as "never ended").
2. Honest forecast check: for each weekday from 21 Sep, the level-specific curve is fitted only
   on earlier days, and compared with a curve that ignores the level.

    PYTHONPATH=src python -m airalerts.levels
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from lifelines import KaplanMeierFitter

from .attacks import load_reports
from .data import build_store, eligible_units
from .dataset import build_m2

LEVELS_START = "2026-09-06"
CHECK_FROM = "2026-09-21"
HORIZONS = {"08:30": 30, "09:00": 60, "10:00": 120, "11:00": 180, "12:00": 240}
ELAPSED = [-1, 30, 120, 10_000]
ELAPSED_LABELS = ["почалась <30 хв тому", "30 хв – 2 год", "понад 2 год"]


def km_clear_probs(g: pd.DataFrame) -> dict:
    kmf = KaplanMeierFitter().fit(g.remaining.clip(lower=0.1), g.event)
    return {f"до {k}": float(1 - kmf.predict(v)) for k, v in HORIZONS.items()}


def describe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.assign(elapsed_bin=pd.cut(df.elapsed, ELAPSED, labels=ELAPSED_LABELS))
    rows = []
    for keys, g in [(("усі", "усі"), df)] + list(df.groupby("level")) + list(df.groupby(["level", "elapsed_bin"], observed=True)):
        if not isinstance(keys, tuple):
            keys = (keys, "усі")
        if len(g) < 15:
            continue
        rows.append(dict(рівень=keys[0], триває=keys[1], n=len(g), **km_clear_probs(g)))
    return pd.DataFrame(rows)


def rolling_check(df: pd.DataFrame, horizon: int = 120) -> pd.DataFrame:
    """Brier of P(clear within horizon) with and without the level, curves fitted on earlier days only."""
    out = []
    for d in sorted(df[(df.day >= CHECK_FROM) & (df.day.dt.weekday < 5)].day.unique()):
        past, today = df[df.day < d], df[df.day == d]
        if past.empty or today.empty:
            continue
        pooled = 1 - KaplanMeierFitter().fit(past.remaining.clip(lower=0.1), past.event).predict(horizon)
        by_level = {lv: 1 - KaplanMeierFitter().fit(g.remaining.clip(lower=0.1), g.event).predict(horizon)
                    for lv, g in past.groupby("level") if len(g) >= 20}
        for r in today[today.event | (today.remaining > horizon)].itertuples():
            y = float(r.remaining <= horizon)
            out.append(dict(day=d, level=r.level, y=y, p_pooled=pooled, p_level=by_level.get(r.level, pooled)))
    res = pd.DataFrame(out)
    return res


def main() -> None:
    store = build_store(Path("data/raw/vadimkin/official_data_uk.csv"), Path("data/interim/telegram_episodes.parquet"))
    reports = load_reports(Path("data/raw/kaggle/missile_attacks_daily.csv"))
    units = eligible_units(store)
    last = (store.coverage[1] - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    df = build_m2(store, reports, LEVELS_START, last, all_days=True)
    df = df[df.unit.isin(units) & df.level.isin(["yellow", "red"])].copy()
    df["level"] = df.level.map({"yellow": "жовта", "red": "червона"})
    Path("results").mkdir(exist_ok=True), Path("figures").mkdir(exist_ok=True)

    tab = describe(df)
    tab.to_csv("results/levels_clear_by_time.csv", index=False)
    print(f"alerts active at 08:00 with a known level, {LEVELS_START} → {last}: {len(df)}")
    print(tab.round(2).to_string(index=False))

    chk = rolling_check(df)
    b_pooled = ((chk.p_pooled - chk.y) ** 2).mean()
    b_level = ((chk.p_level - chk.y) ** 2).mean()
    print(f"\nforecast check (weekdays from {CHECK_FROM}, n={len(chk)}): Brier without level {b_pooled:.4f}, "
          f"with level {b_level:.4f}, BSS {1 - b_level / b_pooled:+.3f}")
    pd.DataFrame([dict(n=len(chk), brier_pooled=b_pooled, brier_level=b_level, bss=1 - b_level / b_pooled)]).to_csv(
        "results/levels_forecast_check.csv", index=False)

    fig, ax = plt.subplots(figsize=(7, 4.2))
    for lv, col in (("жовта", "#e0b400"), ("червона", "#d64545")):
        g = df[df.level == lv]
        kmf = KaplanMeierFitter().fit(g.remaining.clip(lower=0.1), g.event)
        t = np.arange(0, 361, 5)
        ax.step(8 + t / 60, 1 - kmf.predict(t).to_numpy(), where="post", color=col, lw=2, label=f"{lv} (n={len(g)})")
    ax.axvline(10, color="#888", ls="--", lw=1)
    ax.text(10.05, 0.05, "контрольна точка 10:00", color="#666", fontsize=9)
    ax.set_xlim(8, 14), ax.set_ylim(0, 1)
    ax.set_xlabel("година (Київ)"), ax.set_ylabel("частка тривог, що вже закінчились")
    ax.set_title("Тривога о 08:00: коли буде відбій, за рівнем")
    ax.legend(frameon=False, loc="lower right")
    fig.tight_layout()
    fig.savefig("figures/levels_clear_curves.png", dpi=150)


if __name__ == "__main__":
    main()
