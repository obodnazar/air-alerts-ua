"""Evening brief for schools: one line per raion for the next school day.

    PYTHONPATH=src python -m airalerts.brief --day 2026-10-13 [--units "Київ,Бучанський район"]

Uses only data published before 20:00 of the previous evening. Template text, no LLM:
every number in the brief comes straight from the models.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from . import models as M
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
    last_full = (d - pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    hist1 = build_m1(store, reports, "2025-12-15", last_full)
    hist2 = build_m2(store, reports, "2025-12-15", last_full)
    hist1, hist2 = hist1[hist1.unit.isin(keep)], hist2[hist2.unit.isin(keep)]
    fit1 = M.fit_m1(hist1[hist1.issue == issue])
    fit2 = M.fit_m2(hist2)
    now = build_m1(store, reports, day, day)
    now = now[(now.issue == issue) & now.unit.isin(units or keep)].reset_index(drop=True)
    p = M.predict_m1(fit1, now)
    if tau_plan is None:
        s = Path("results/summary.json")
        tau_plan = json.load(open(s))["policy_tau_from_val"]["B_plan_20"] if s.exists() else 0.35
    out = pd.DataFrame(dict(
        unit=now.unit, oblast=now.oblast,
        p_alert_08=p.logit, usual=p.climatology,
        p_clear_by_10=now.oblast.map(fit2["oblast_km"]).fillna(fit2["pooled_km"]),
    ))
    out["p_remote_day"] = out.p_alert_08 * (1 - out.p_clear_by_10)
    out["advice"] = out.p_remote_day.map(lambda x: "розгляньте дистанційний день" if x > tau_plan else "плануйте очний день")
    out.attrs.update(day=d, issue=T, issue_label=issue, tau=tau_plan, data_end=store.coverage[1])
    return out.sort_values("p_alert_08", ascending=False)


def render(b: pd.DataFrame) -> str:
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
    ap.add_argument("--units", help="comma-separated unit names; default all")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    b = make_brief(a.day, a.units.split(",") if a.units else None)
    text = render(b)
    out = a.out or Path("results") / f"brief_{a.day}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
