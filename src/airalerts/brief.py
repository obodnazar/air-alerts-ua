"""Evening brief for schools: one line per raion for the next school day.

    PYTHONPATH=src python -m airalerts.brief --day 2026-10-13 [--units "Київ,Бучанський район"]

Uses only data published before 20:00 of the previous evening. Template text, no LLM:
every number in the brief comes straight from the models.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import models as M
from .clearance import WINDOW, fit_predict
from .attacks import load_reports
from .data import build_store, eligible_units
from .dataset import build_m1, build_m2, kyiv

DOW = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"]


def make_brief(day: str, units: list[str] | None = None, tau_plan: float | None = None, issue: str = "20:00") -> pd.DataFrame:
    store = build_store(Path("data/raw/vadimkin/official_data_uk.csv"), Path("data/interim/telegram_episodes.parquet"))
    reports = load_reports(Path("data/raw/kaggle/missile_attacks_daily.csv"))
    keep = eligible_units(store)
    d = pd.Timestamp(day)
    T = kyiv(d - pd.Timedelta(days=1), "20:00") if issue == "20:00" else kyiv(d, issue)
    if store.coverage[1] < T - pd.Timedelta(hours=1):
        print(f"WARNING: data end at {store.coverage[1]}, brief issue time {T}: update the scrape first")
    if issue == "08:00":
        return update_08(store, reports, keep, d, units)
    last_full = (d - pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    hist1 = build_m1(store, reports, "2025-12-15", last_full)
    hist1 = hist1[hist1.unit.isin(keep)]
    fit1 = M.fit_m1(hist1[hist1.issue == issue])
    recent = build_m2(store, reports, (d - pd.Timedelta(days=WINDOW)).strftime("%Y-%m-%d"),
                      (d - pd.Timedelta(days=1)).strftime("%Y-%m-%d"), all_days=True)
    recent = recent[recent.unit.isin(keep) & (recent.issue_time < T)]
    now = build_m1(store, reports, day, day)
    now = now[(now.issue == issue) & now.unit.isin(units or keep)].reset_index(drop=True)
    p = M.predict_m1(fit1, now)
    if tau_plan is None:
        s = Path("results/summary.json")
        tau_plan = json.load(open(s))["policy_tau_from_val"]["B_plan_20"] if s.exists() else 0.35
    out = pd.DataFrame(dict(
        unit=now.unit, oblast=now.oblast,
        p_alert_08=p.logit, usual=p.climatology,
        # per raion, refitted on the last 6 weeks (clearance.py); was one curve per oblast until 12 Oct
        p_clear_by_10=fit_predict(recent, now[["unit", "oblast"]], with_08=False),
    ))
    out["p_remote_day"] = out.p_alert_08 * (1 - out.p_clear_by_10)
    out["advice"] = out.p_remote_day.map(lambda x: "розгляньте дистанційний день" if x > tau_plan else "плануйте очний день")
    out.attrs.update(day=d, issue=T, issue_label=issue, tau=tau_plan, data_end=store.coverage[1])
    return out.sort_values("p_alert_08", ascending=False)


def update_08(store, reports, keep, d, units=None) -> pd.DataFrame:
    """At 08:00, for raions under alert now: chance of the all-clear by 10:00, using level and elapsed time."""
    T = kyiv(d, "08:00")
    recent = build_m2(store, reports, (d - pd.Timedelta(days=WINDOW)).strftime("%Y-%m-%d"),
                      (d - pd.Timedelta(days=1)).strftime("%Y-%m-%d"), all_days=True)
    recent = recent[recent.unit.isin(keep)]
    now = build_m2(store, reports, d.strftime("%Y-%m-%d"), d.strftime("%Y-%m-%d"), all_days=True)
    now = now[now.unit.isin(units or keep)].reset_index(drop=True)
    p = fit_predict(recent, now, with_08=True)
    s = Path("results/summary.json")
    tau = json.load(open(s))["policy_tau_from_val"]["C_call_08"] if s.exists() else 0.35
    out = pd.DataFrame(dict(unit=now.unit, oblast=now.oblast, level=now.level, elapsed_min=now.elapsed, p_clear_by_10=p))
    out["advice"] = np.where(out.p_clear_by_10 < tau, "оголосіть дистанційний день зараз", "чекайте контрольної точки 10:00")
    out.attrs.update(day=d, issue=T, issue_label="08:00", tau=tau, data_end=store.coverage[1])
    return out.sort_values("p_clear_by_10")


def render_08(b: pd.DataFrame) -> str:
    d = b.attrs["day"]
    lv = {"red": "🔴 червона", "yellow": "🟡 жовта", "unknown": "тривога"}
    lines = [f"# Шкільний ранок · оновлення о 08:00 · {DOW[d.weekday()]} {d:%d.%m.%Y}",
             f"Райони, де о 08:00 триває тривога: {len(b)}. Дані до "
             f"{b.attrs['data_end'].tz_convert('Europe/Kyiv'):%d.%m %H:%M}. Не замінює офіційних сигналів.", ""]
    for r in b.itertuples():
        lines.append(f"- **{r.unit}**: {lv.get(r.level, 'тривога')}, триває {r.elapsed_min:.0f} хв; "
                     f"відбій до 10:00 — {r.p_clear_by_10:.0%} → {r.advice}.")
    lines += ["", f"Поріг «оголосити зараз»: {b.attrs['tau']:.0%}, обраний на валідації (серпень 2026)."]
    return "\n".join(lines)


def render(b: pd.DataFrame) -> str:
    if b.attrs.get("issue_label") == "08:00":
        return render_08(b)
    d = b.attrs["day"]
    lines = [f"# Шкільний ранок · {DOW[d.weekday()]} {d:%d.%m.%Y}",
             f"Прогноз станом на {b.attrs['issue'].tz_convert('Europe/Kyiv'):%d.%m %H:%M}. Дані до {b.attrs['data_end'].tz_convert('Europe/Kyiv'):%d.%m %H:%M}. "
             "Не замінює офіційних сигналів.", ""]
    for r in b.itertuples():
        lines.append(
            f"- **{r.unit}**: шанс тривоги о 08:00 — {r.p_alert_08:.0%} (зазвичай {r.usual:.0%}); "
            f"якщо буде, відбій до 10:00 — {r.p_clear_by_10:.0%}; повністю дистанційний день — {r.p_remote_day:.0%}. "
            f"→ {r.advice}."
        )
    lines += ["", f"Поріг для поради «дистанційний день»: {b.attrs['tau']:.0%}, обраний на валідації (серпень 2026)."]
    lv = Path("results/levels_clear_by_time.csv")
    if lv.exists():
        t = pd.read_csv(lv)
        t = t[(t["рівень"] != "усі") & (t["триває"] != "усі")]
        lines += ["", "## Оновлення о 08:00: якщо тривога вже йде",
                  "Шанс відбою до контрольної точки за кольором і тим, скільки тривога вже триває "
                  "(тривоги о 08:00, 06.09–10.10.2026):", "",
                  "| тривога | триває | до 09:00 | до 10:00 | до 11:00 |", "|---|---|---|---|---|"]
        for r in t.itertuples(index=False):
            lines.append(f"| {r[0]} | {r[1]} | {r[4]:.0%} | {r[5]:.0%} | {r[6]:.0%} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--issue", default="20:00", choices=["20:00", "07:00", "08:00"])
    ap.add_argument("--units", help="comma-separated unit names; default all")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    b = make_brief(a.day, a.units.split(",") if a.units else None, issue=a.issue)
    text = render(b)
    out = a.out or Path("results") / f"brief_{a.day}_{a.issue.replace(':', '')}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
