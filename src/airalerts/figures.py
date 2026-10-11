"""Figures for the README from results/*.csv.  PYTHONPATH=src python -m airalerts.figures"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

R, F = Path("results"), Path("figures")


def bss_bars() -> None:
    s = json.load(open(R / "summary.json"))["test"]
    rows = [("тривога о 08:00,\nпрогноз о 20:00", s["m1_20:00"]["bss_vs_strongest_ci"]["logit"]),
            ("тривога о 08:00,\nпрогноз о 07:00", s["m1_07:00"]["bss_vs_strongest_ci"]["logit"]),
            ("відбій до 10:00,\nякщо тривога о 08:00", s["m2"]["bss_vs_km_ci"]["logit"])]
    fig, ax = plt.subplots(figsize=(7, 3.6))
    for i, (lab, (est, lo, hi)) in enumerate(rows):
        col = "#2e7d32" if lo > 0 else "#9e9e9e"
        ax.errorbar(est * 100, i, xerr=[[est * 100 - lo * 100], [hi * 100 - est * 100]], fmt="o", color=col, capsize=5, lw=2)
        ax.text(hi * 100 + 0.8, i, f"{est:+.1%}", va="center", color=col)
    ax.axvline(0, color="#555", lw=1)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows]), ax.invert_yaxis()
    ax.set_xlabel("покращення Brier проти найсильнішого простого правила, % (95% CI)")
    ax.set_title("Тест: 1 вересня – 9 жовтня 2026, 104 одиниці")
    fig.tight_layout(), fig.savefig(F / "test_skill.png", dpi=150), plt.close(fig)


def policies() -> None:
    p = pd.read_csv(R / "test_policies.csv", index_col=0)
    names = {"A_status_quo_cp10": "A · як зараз (10:00)", "B_plan_at_20": "B · план з вечора",
             "C_call_at_08": "C · рішення о 08:00", "D_best_cp_per_raion": "D · своя контр. точка",
             "C+D": "C + D", "oracle": "знає майбутнє"}
    p = p.rename(index=names)
    fig, ax = plt.subplots(figsize=(7, 3.8))
    ax.scatter(p.limbo_h * 60, p.in_person_h, s=60, color=["#9e9e9e", "#bdbdbd", "#5b8def", "#e4572e", "#8e44ad", "#2e7d32"])
    for n, r in p.iterrows():
        ax.annotate(n, (r.limbo_h * 60, r.in_person_h), xytext=(6, 4), textcoords="offset points", fontsize=9)
    ax.set_xlabel("хвилин невизначеності після 08:00 (середнє на район-день)")
    ax.set_ylabel("очних годин 08:00–15:00")
    ax.set_title("Стратегії школи на тесті (вересень–жовтень 2026)")
    fig.tight_layout(), fig.savefig(F / "test_policies.png", dpi=150), plt.close(fig)


if __name__ == "__main__":
    F.mkdir(exist_ok=True)
    bss_bars()
    policies()
