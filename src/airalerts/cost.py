"""The price of alerts for schools (idea 4, applied to school mornings). Descriptive, no models.

1. Zoning dividend: school mornings that started under alert, actual (raion-level) vs a
   counterfactual where every alert covers the whole oblast.
2. Yellow vs red: school hours (weekdays 08:00-15:00) under each level since 8 Sep 2026.
   Under КМУ №1092 business may work during yellow if it has a shelter, but schools go to
   shelter at both levels (MoE clarification), so yellow alerts cost school time specifically.
3. Control point: for each raion, in-person hours under the MoE rule with control point
   09/10/11/12, on Jan-May and Sep-Oct 2026 school days.

    PYTHONPATH=src python -m airalerts.cost
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .data import EXCLUDED_OBLASTS, OCCUPIED, _merge, _mins, build_store, eligible_units
from .dataset import kyiv
from .policies import react

SCHOOL_DAYS = [d for d in pd.date_range("2026-01-12", "2026-05-29") if d.weekday() < 5] + \
              [d for d in pd.date_range("2026-09-01", "2026-10-09") if d.weekday() < 5]


def oblast_wide_store(store):
    """Counterfactual: any alert in any unit of an oblast covers every unit of that oblast."""
    starts, ends = {}, {}
    by_oblast = {}
    for u, o in store.oblast_of.items():
        by_oblast.setdefault(o, []).append(u)
    for o, us in by_oblast.items():
        s = np.concatenate([store.starts[u] for u in us])
        e = np.concatenate([store.ends[u] for u in us])
        ms, me = _merge(s, e)
        for u in us:
            starts[u], ends[u] = ms, me
    cf = type(store)(starts, ends, store.oblast_of, store.coverage, store.raw)
    return cf


def zoning_dividend(store, units) -> pd.DataFrame:
    cf = oblast_wide_store(store)
    rows = []
    for d in SCHOOL_DAYS:
        t = kyiv(d, "08:00")
        for u in units:
            rows.append(dict(day=d, unit=u, oblast=store.oblast_of[u],
                             actual=store.active_until(u, t) is not None,
                             oblast_wide=cf.active_until(u, t) is not None))
    return pd.DataFrame(rows)


def level_school_minutes(store, units) -> pd.DataFrame:
    """Minutes of weekday 08:00-15:00 under yellow / red per unit, from level timelines (since 8 Sep)."""
    tg = store.raw[store.raw.levels.notna() & store.raw.unit.isin(units)]
    days = [d for d in pd.date_range("2026-09-08", "2026-10-09") if d.weekday() < 5]
    windows = [(kyiv(d, "08:00"), kyiv(d, "15:00")) for d in days]
    out = {"yellow": 0.0, "red": 0.0}
    for r in tg.itertuples():
        seg = json.loads(r.levels)
        times = [pd.Timestamp(t) for t, _ in seg] + [r.finished_at]
        for (t0, lv), t1 in zip([(times[i], seg[i][1]) for i in range(len(seg))], times[1:]):
            if lv not in out:
                continue
            for a, b in windows:
                lo, hi = max(t0, a), min(t1, b)
                if hi > lo:
                    out[lv] += (hi - lo).total_seconds() / 60
    total = len(units) * len(days) * 7 * 60
    return pd.DataFrame([dict(level=k, school_minutes=v, share_of_school_time=v / total) for k, v in out.items()])


def control_points(store, units) -> pd.DataFrame:
    rows = []
    for u in units:
        alert, clear = [], []
        for d in SCHOOL_DAYS:
            t = kyiv(d, "08:00")
            end = store.active_until(u, t)
            alert.append(end is not None)
            clear.append(8 + (end - int(t.value // 60_000_000_000)) / 60 if end else 8.0)
        alert, clear = np.array(alert), np.array(clear)
        rec = dict(unit=u, oblast=store.oblast_of[u], alert_share=alert.mean())
        for cp in (9.0, 10.0, 11.0, 12.0):
            ip, lb = react(alert, clear, np.full(len(alert), cp))
            rec[f"in_person_{int(cp)}"] = ip.mean()
            rec[f"remote_days_{int(cp)}"] = (alert & (clear > cp)).mean()
            rec[f"limbo_{int(cp)}"] = lb.mean()
        rows.append(rec)
    return pd.DataFrame(rows)


def main() -> None:
    store = build_store(Path("data/raw/vadimkin/official_data_uk.csv"), Path("data/interim/telegram_episodes.parquet"))
    units = eligible_units(store)
    Path("results").mkdir(exist_ok=True), Path("figures").mkdir(exist_ok=True)

    z = zoning_dividend(store, units)
    zs = dict(unit_mornings=len(z), alert_at_08_actual=z.actual.mean(), alert_at_08_oblast_wide=z.oblast_wide.mean(),
              mornings_saved=int(z.oblast_wide.sum() - z.actual.sum()))
    zo = z.groupby("oblast")[["actual", "oblast_wide"]].mean().assign(saved_pp=lambda x: x.oblast_wide - x.actual)
    zo.sort_values("saved_pp", ascending=False).to_csv("results/cost_zoning_by_oblast.csv")
    print("zoning dividend:", {k: round(v, 3) if isinstance(v, float) else v for k, v in zs.items()})

    lv = level_school_minutes(store, units)
    lv.to_csv("results/cost_levels_school_time.csv", index=False)
    print(lv.round(4).to_string(index=False))

    cp = control_points(store, units)
    cp.to_csv("results/cost_control_points.csv", index=False)
    mid = cp[(cp.alert_share >= 0.05) & (cp.alert_share < 0.6)]
    print(f"control points, {len(mid)} mid-intensity raions (5-60% mornings under alert):")
    print(mid[[c for c in cp.columns if c.startswith(("in_person", "remote_days", "limbo"))]].mean().round(3).to_string())
    json.dump(dict(zoning=zs, levels=lv.to_dict("records"),
                   control_points_mid=mid[[c for c in cp.columns if c[0] in "irl" and c != "unit"]].mean().to_dict(),
                   n_mid=len(mid)), open("results/cost_summary.json", "w"), indent=2, default=float)

    fig, ax = plt.subplots(figsize=(6.5, 4))
    xs = [9, 10, 11, 12]
    ax.plot(xs, [mid[f"remote_days_{x}"].mean() * 100 for x in xs], marker="o", color="#d64545", label="повністю дистанційні дні, %")
    ax2 = ax.twinx()
    ax2.plot(xs, [mid[f"limbo_{x}"].mean() * 60 for x in xs], marker="s", color="#5b8def", label="хвилин невизначеності на день")
    ax.set_xticks(xs, [f"{x}:00" for x in xs]), ax.set_xlabel("контрольна точка школи")
    ax.set_ylabel("дистанційні дні, %", color="#d64545"), ax2.set_ylabel("хв невизначеності", color="#5b8def")
    ax.set_title(f"Ціна вибору контрольної точки ({len(mid)} районів середньої інтенсивності)")
    fig.tight_layout()
    fig.savefig("figures/cost_control_point.png", dpi=150)


if __name__ == "__main__":
    main()
