"""Post-hoc experiments (12 Oct), decided on validation (Aug 2026) like the registered ones:
  1. a later evening forecast: 20:00 vs 21:30 vs 22:00;
  2. neighbouring raions as features (adjacency from raion boundaries, geo.neighbours).
The test window is reported only as a secondary, post-hoc check.

    PYTHONPATH=src python -m airalerts.experiments
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import models as M
from .attacks import load_reports
from .data import build_store, eligible_units
from .dataset import build_m1

NB = ["nb_share_now", "nb_min_6h"]
WIN = dict(train=("2025-12-15", "2026-07-31"), val=("2026-08-01", "2026-08-31"), test=("2026-09-01", "2026-10-09"))


def run(split_eval: str, store, reports, units, nbrs) -> list[dict]:
    rows = []
    for evening in ("20:00", "21:30", "22:00"):
        tr = build_m1(store, reports, *WIN["train"], evening=evening, neighbours=nbrs)
        ev = build_m1(store, reports, *WIN[split_eval], evening=evening, neighbours=nbrs)
        if split_eval == "test":
            tr = pd.concat([tr, build_m1(store, reports, *WIN["val"], evening=evening, neighbours=nbrs)], ignore_index=True)
        tr, ev = tr[tr.unit.isin(units)], ev[ev.unit.isin(units)]
        for issue in ((evening, "07:00") if evening == "20:00" else (evening,)):
            t, e = tr[tr.issue == issue], ev[ev.issue == issue].reset_index(drop=True)
            p_base = M.predict_m1(M.fit_m1(t, M.M1_FEATURES), e)
            p_nb = M.predict_m1(M.fit_m1(t, M.M1_FEATURES + NB), e)
            keep, gain, se = M.keep_extra(e.y, p_base.logit, p_nb.logit, e.day)
            best_base = min(("climatology", "rolling_markov", "persistence"), key=lambda c: ((p_base[c] - e.y) ** 2).mean())
            b = lambda p: float(((p - e.y) ** 2).mean())  # noqa: E731
            rows.append(dict(split=split_eval, issue=issue, n=len(e), brier_climatology=b(p_base.climatology),
                             strongest_baseline=best_base, brier_strongest=b(p_base[best_base]),
                             brier_model=b(p_base.logit), brier_model_nb=b(p_nb.logit),
                             nb_gain=gain, nb_se=se, keep_neighbours=keep))
    return rows


def main() -> None:
    store = build_store(Path("data/raw/vadimkin/official_data_uk.csv"), Path("data/interim/telegram_episodes.parquet"))
    reports = load_reports(Path("data/raw/kaggle/missile_attacks_daily.csv"))
    units = eligible_units(store)
    nbrs = json.load(open("data/snapshot/raion_neighbours.json", encoding="utf-8"))
    res = pd.DataFrame(run("val", store, reports, units, nbrs) + run("test", store, reports, units, nbrs))
    res["bss_model_vs_strongest"] = 1 - res.brier_model / res.brier_strongest
    res["bss_model_nb_vs_strongest"] = 1 - res.brier_model_nb / res.brier_strongest
    res.to_csv("results/posthoc_evening_neighbours.csv", index=False)
    cols = ["split", "issue", "n", "brier_climatology", "strongest_baseline", "brier_strongest", "brier_model",
            "brier_model_nb", "keep_neighbours", "bss_model_vs_strongest", "bss_model_nb_vs_strongest"]
    print(res[cols].round(4).to_string(index=False))


if __name__ == "__main__":
    main()
