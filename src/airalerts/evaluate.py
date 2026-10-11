"""End-to-end run, following PREREG.md.

    PYTHONPATH=src python -m airalerts.evaluate            # validation only (model choice, thresholds)
    PYTHONPATH=src python -m airalerts.evaluate --test     # + the single test run

Writes results/*.csv, results/summary.json and figures/*.png.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import models as M
from . import policies as P
from .attacks import load_reports
from .data import build_store, eligible_units
from .dataset import build_m1, build_m2

WIN = dict(train=("2025-12-15", "2026-07-31"), val=("2026-08-01", "2026-08-31"), test=("2026-09-01", "2026-10-09"))
RES, FIG = Path("results"), Path("figures")


def load_tables(store, reports, units):
    t = {}
    for k, (a, b) in WIN.items():
        m1 = build_m1(store, reports, a, b)
        m2 = build_m2(store, reports, a, b)
        t[k] = (m1[m1.unit.isin(units)].reset_index(drop=True), m2[m2.unit.isin(units)].reset_index(drop=True))
        print(f"{k}: M1 rows {len(t[k][0])} (alert@08 {t[k][0].y.mean():.3f}), M2 rows {len(t[k][1])}")
    return t


def run_m1(train, evalset, issue, tag):
    tr, ev = train[train.issue == issue], evalset[evalset.issue == issue]
    fit = M.fit_m1(tr)
    pr = M.predict_m1(fit, ev)
    for c in ("logit", "hgb"):
        M.guard(ev.y, pr[c], f"M1 {issue} {c}")
    tab = M.table(ev.y, pr, "climatology")
    subsets = {}
    for name, mask in (("alert_at_issue", ev.active_now), ("no_alert_at_issue", ~ev.active_now)):
        subsets[name] = M.table(ev.y[mask], pr[mask], "climatology")
    return fit, pr, tab, subsets


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="run the single registered test evaluation")
    args = ap.parse_args()
    RES.mkdir(exist_ok=True), FIG.mkdir(exist_ok=True)

    store = build_store(Path("data/raw/vadimkin/official_data_uk.csv"), Path("data/interim/telegram_episodes.parquet"))
    reports = load_reports(Path("data/raw/kaggle/missile_attacks_daily.csv"))
    units = eligible_units(store)
    print(f"eligible units: {len(units)}")
    T = load_tables(store, reports, units)
    summary = dict(units=len(units), windows=WIN)

    # ---------------- validation: model choice ----------------
    for issue in ("20:00", "07:00"):
        _, _, tab, sub = run_m1(T["train"][0], T["val"][0], issue, "val")
        tab.to_csv(RES / f"val_m1_{issue.replace(':', '')}.csv")
        print(f"\nVAL M1 issue {issue}\n", tab.round(4).to_string())
    m2fit = M.fit_m2(T["train"][1])
    m2val = M.predict_m2(m2fit, T["val"][1])
    tab2 = M.table(T["val"][1].clear_by_10, m2val, "km_pooled")
    tab2.to_csv(RES / "val_m2.csv")
    print("\nVAL M2 (all-clear by 10:00)\n", tab2.round(4).to_string())

    # thresholds for the policies, chosen on validation
    trval1 = pd.concat([T["train"][0], T["val"][0]])
    trval2 = pd.concat([T["train"][1], T["val"][1]])
    val_days = decision_days(T["train"][0], T["val"][0], T["val"][1], m2fit)
    taus = np.round(np.arange(0.05, 0.96, 0.05), 2)
    grid = []
    for tp in taus:
        grid.append(dict(policy="B_plan_20", tau=tp, **P.summarise(P.evaluate(val_days, tau_plan=tp))))
        grid.append(dict(policy="C_call_08", tau=tp, **P.summarise(P.evaluate(val_days, tau_call=tp))))
    grid = pd.DataFrame(grid)
    grid.to_csv(RES / "val_policy_grid.csv", index=False)
    best_tau = {p: float(g.loc[g["J_lam0.5"].idxmax(), "tau"]) for p, g in grid.groupby("policy")}
    summary["policy_tau_from_val"] = best_tau
    print("\nthresholds chosen on validation (lam=0.5):", best_tau)

    if not args.test:
        json.dump(summary, open(RES / "summary_val.json", "w"), indent=2, default=str)
        return

    # ---------------- the single test run ----------------
    if getattr(store, "gap", None):
        raise SystemExit(f"coverage gap {store.gap}: finish the Telegram scrape before the test run")
    summary["test"] = {}
    for issue in ("20:00", "07:00"):
        fit, pr, tab, sub = run_m1(trval1, T["test"][0], issue, "test")
        ev = T["test"][0][T["test"][0].issue == issue]
        base = ["climatology", "persistence", "rolling_markov"]
        strongest = tab.loc[base, "brier"].idxmin()
        boot = {m: M.block_bootstrap_diff(ev.y, pr[m], pr[strongest], ev.day) for m in ("logit", "hgb")}
        tab.to_csv(RES / f"test_m1_{issue.replace(':', '')}.csv")
        for k, v in sub.items():
            v.to_csv(RES / f"test_m1_{issue.replace(':', '')}_{k}.csv")
        summary["test"][f"m1_{issue}"] = dict(
            strongest_baseline=strongest, table=tab.round(4).to_dict(),
            bss_vs_strongest_ci={m: [round(x, 4) for x in v] for m, v in boot.items()},
            subsets={k: v.round(4)[["n", "base_rate", "brier"]].to_dict() for k, v in sub.items()},
        )
        reliability_plot(ev.y, pr, issue)
        print(f"\nTEST M1 {issue}\n", tab.round(4).to_string(), "\nBSS vs", strongest, boot)

    m2fit_tv = M.fit_m2(trval2)
    ev2 = T["test"][1]
    pr2 = M.predict_m2(m2fit_tv, ev2)
    tab2 = M.table(ev2.clear_by_10, pr2, "km_pooled")
    boot2 = {m: M.block_bootstrap_diff(ev2.clear_by_10, pr2[m], pr2["km_pooled"], ev2.day) for m in ("logit", "weibull_aft")}
    tab2.to_csv(RES / "test_m2.csv")
    summary["test"]["m2"] = dict(table=tab2.round(4).to_dict(), c_index_aft=M.c_index(ev2, pr2.aft_median),
                                 bss_vs_km_ci={m: [round(x, 4) for x in v] for m, v in boot2.items()})
    print("\nTEST M2\n", tab2.round(4).to_string(), boot2)

    # policies on test
    test_days = decision_days(trval1, T["test"][0], T["test"][1], m2fit_tv)
    hist_days = pd.concat([decision_days(None, *T[k], None, only_outcomes=True) for k in ("train", "val")])
    cp_by_unit = P.best_control_point(hist_days)
    pol = {
        "A_status_quo_cp10": P.evaluate(test_days),
        "B_plan_at_20": P.evaluate(test_days, tau_plan=best_tau["B_plan_20"]),
        "C_call_at_08": P.evaluate(test_days, tau_call=best_tau["C_call_08"]),
        "D_best_cp_per_raion": P.evaluate(test_days, cp_by_unit=cp_by_unit),
        "C+D": P.evaluate(test_days, cp_by_unit=cp_by_unit, tau_call=best_tau["C_call_08"]),
        "oracle": P.evaluate(test_days, oracle=True),
    }
    ptab = pd.DataFrame({k: P.summarise(v) for k, v in pol.items()}).T
    ptab.to_csv(RES / "test_policies.csv")
    summary["test"]["policies"] = ptab.round(4).to_dict()
    summary["test"]["control_points"] = pd.Series(cp_by_unit).value_counts().to_dict()
    print("\nTEST policies (per unit-day)\n", ptab.round(3).to_string())
    json.dump(summary, open(RES / "summary.json", "w"), indent=2, default=str, ensure_ascii=False)


def decision_days(hist_m1, m1, m2, m2fit, only_outcomes=False):
    """Per (unit, day): alert at 08:00, all-clear hour, P(remote day) at 20:00, P(clear by 10) at 08:00.
    The 20:00 model is fitted on hist_m1 only (data before the evaluated window)."""
    d = m1[m1.issue == "20:00"][["unit", "oblast", "day", "y"]].rename(columns={"y": "alert"}).copy()
    clear = (m2.set_index(["unit", "day"]).remaining / 60 + 8).to_dict()
    d["clear_h"] = [clear.get((u, x), np.nan) for u, x in zip(d.unit, d.day)]
    if only_outcomes:
        return d.reset_index(drop=True)
    fit = M.fit_m1(hist_m1[hist_m1.issue == "20:00"])
    p20 = M.predict_m1(fit, m1[m1.issue == "20:00"])["logit"].to_numpy()
    d["p_remote_20"] = p20 * (1 - m2fit["pooled_km"])
    pc = dict(zip(zip(m2.unit, m2.day), M.predict_m2(m2fit, m2)["logit"].to_numpy()))
    d["p_clear"] = [pc.get((u, x), np.nan) for u, x in zip(d.unit, d.day)]
    return d.reset_index(drop=True)


def reliability_plot(y, pr, issue):
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 1], [0, 1], color="#999", lw=1, ls="--")
    for c, col in (("climatology", "#9aa5b1"), ("rolling_markov", "#5b8def"), ("logit", "#e4572e")):
        r = M.reliability(y, pr[c])
        ax.plot(r.mean_p, r.observed, marker="o", label=c, color=col)
    ax.set_xlabel("forecast probability"), ax.set_ylabel("observed frequency")
    ax.set_title(f"Alert at 08:00, forecast at {issue} (test)")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(FIG / f"reliability_m1_{issue.replace(':', '')}.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
