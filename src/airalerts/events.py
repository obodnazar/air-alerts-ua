"""Event watcher on keywords (no LLM) and a test of whether news predicts alerts.

Classes of posts:
  MASS_WARNING  a mass attack is expected or its precursors are reported
                (strategic bombers, MiG-31K, Kalibr carriers, launches)
  TRUCE         truce / ceasefire / moratorium
  RETALIATION   threats of a "retaliation strike"
Duplicates of the same class within 3 hours count as one event.

Hypotheses, tested on Dec 2025 – Oct 2026:
  H1  a MASS_WARNING posted before the 20:00 forecast predicts a mass night and an alert at 08:00,
      beyond what the model already knows (checked with the same 1-SE rule as other features);
  H2  alert load changes after TRUCE news (descriptive: too few events for a test).

    PYTHONPATH=src python -m airalerts.events
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import models as M
from .attacks import load_reports
from .data import build_store, eligible_units
from .dataset import build_m1, kyiv

CLASSES = {
    "MASS_WARNING": re.compile(
        r"(можлив|загроз|очіку|готу|попереджа|ймовірн)\w*\s.{0,40}масован|масован\w*\s+(атак|удар|обстріл)\w*.{0,30}(можлив|очіку|вночі|сьогодні)"
        r"|ту-?\s?95|ту-?\s?160|ту-?\s?22|стратегічн\w* авіац|міг-?\s?31|носі\w* калібр|пуски? .{0,25}(калібр|шахед|ракет)"),
    "TRUCE": re.compile(r"перемир|припинення вогню|мораторі"),
    "RETALIATION": re.compile(r"удар\w* у відповідь|помст|відплат|відповідь буде"),
}
NOT_NEWS = re.compile(r"відбій|^\W*мінус")


def load_events(paths: list[Path]) -> pd.DataFrame:
    rows, seen = [], set()
    for p in paths:
        for line in p.open(encoding="utf-8"):
            if not line.strip():
                continue
            x = json.loads(line)
            if (x["channel"], x["id"]) in seen:  # parallel range scrapes overlap
                continue
            seen.add((x["channel"], x["id"]))
            t = x["text"].lower()
            if NOT_NEWS.search(t):
                continue
            for cls, rx in CLASSES.items():
                if rx.search(t):
                    rows.append(dict(source=x["channel"], published_at=pd.Timestamp(x["published_at"]), cls=cls, text=x["text"][:160]))
    ev = pd.DataFrame(rows).sort_values("published_at")
    keep, last = [], {}
    for r in ev.itertuples():  # 3-hour de-duplication per (source, class)
        k = (r.source, r.cls)
        if k not in last or r.published_at - last[k] > pd.Timedelta(hours=3):
            keep.append(r.Index)
            last[k] = r.published_at
    return ev.loc[keep].reset_index(drop=True)


def evening_flags(ev: pd.DataFrame, days, cls: str, source: str | None = None) -> pd.Series:
    """For target day d: was there a `cls` event between 12:00 and 20:00 the evening before (Kyiv)?"""
    e = ev[(ev.cls == cls) & ((ev.source == source) if source else True)]
    out = {}
    for d in days:
        prev = d - pd.Timedelta(days=1)
        a, b = kyiv(prev, "12:00"), kyiv(prev, "20:00")
        out[d] = bool(((e.published_at >= a) & (e.published_at < b)).any())
    return pd.Series(out)


def main() -> None:
    store = build_store(Path("data/raw/vadimkin/official_data_uk.csv"), Path("data/interim/telegram_episodes.parquet"))
    reports = load_reports(Path("data/raw/kaggle/missile_attacks_daily.csv"))
    units = eligible_units(store)
    tg = Path("data/raw/telegram")
    ev = load_events(sorted(tg.glob("lachentyt*.jsonl")) + sorted(tg.glob("kpszsu*.jsonl")))
    ev.to_csv("results/events.csv", index=False)
    print("events by source and class:\n", ev.groupby(["source", "cls"]).size().to_string())

    # targets per day: mass night (national night load) and Kaggle mass report
    days = [d for d in pd.date_range("2025-12-16", "2026-10-09")]
    night = pd.Series({d: np.mean([store.minutes_in(u, kyiv(d, "00:00"), kyiv(d, "07:00")) for u in units]) / 420 for d in days})
    rep = reports.assign(d=pd.to_datetime(reports.report_date)).set_index("d").mass
    res = []
    for src in ("lachentyt", "kpszsu", None):
        flag = evening_flags(ev, days, "MASS_WARNING", src)
        for target, y in (("night_load>40%", night > 0.4), ("Kaggle mass report", rep.reindex(days).fillna(False).astype(bool))):
            y = y.reindex(flag.index).astype(bool)
            p1, p0 = y[flag].mean(), y[~flag].mean()
            res.append(dict(source=src or "any", target=target, warn_days=int(flag.sum()), days=len(flag),
                            p_if_warning=p1, p_if_not=p0, lift=p1 / p0 if p0 else np.nan,
                            precision=p1, recall=(y & flag).sum() / max(y.sum(), 1)))
    h1 = pd.DataFrame(res)
    h1.to_csv("results/events_h1_lift.csv", index=False)
    print("\nH1 (a) — does an evening warning precede a mass night?\n", h1.round(3).to_string(index=False))

    # H1 (b): does the warning add to the 20:00 model? same 1-SE rule, validation and test
    flag_any = evening_flags(ev, pd.date_range("2025-12-15", "2026-10-09"), "MASS_WARNING")
    tr = build_m1(store, reports, "2025-12-15", "2026-07-31")
    out = []
    for split, (a, b) in {"val": ("2026-08-01", "2026-08-31"), "test": ("2026-09-01", "2026-10-09")}.items():
        e = build_m1(store, reports, a, b)
        t = tr if split == "val" else pd.concat([tr, build_m1(store, reports, "2026-08-01", "2026-08-31")], ignore_index=True)
        t, e = t[(t.issue == "20:00") & t.unit.isin(units)].copy(), e[(e.issue == "20:00") & e.unit.isin(units)].reset_index(drop=True)
        t["warn"], e["warn"] = t.day.map(flag_any).astype(float), e.day.map(flag_any).astype(float)
        pb = M.predict_m1(M.fit_m1(t, M.M1_FEATURES), e).logit
        pw = M.predict_m1(M.fit_m1(t, M.M1_FEATURES + ["warn"]), e).logit
        keep, gain, se = M.keep_extra(e.y, pb, pw, e.day)
        out.append(dict(split=split, n=len(e), brier_base=((pb - e.y) ** 2).mean(), brier_with_warning=((pw - e.y) ** 2).mean(),
                        gain=gain, se=se, passes_1se=keep))
    h1b = pd.DataFrame(out)
    h1b.to_csv("results/events_h1_model.csv", index=False)
    print("\nH1 (b) — does it add to the 20:00 forecast?\n", h1b.round(5).to_string(index=False))

    # H2: alert load around TRUCE news clusters (descriptive)
    tr_ev = ev[(ev.cls == "TRUCE")].published_at.dt.tz_convert("Europe/Kyiv").dt.normalize().dt.tz_localize(None)
    daily = pd.Series({d: np.mean([store.minutes_in(u, kyiv(d, "00:00"), kyiv(d + pd.Timedelta(days=1), "00:00")) for u in units]) / 60
                       for d in pd.date_range("2025-12-10", store.coverage[1].tz_convert("Europe/Kyiv").normalize().tz_localize(None) - pd.Timedelta(days=1))})
    h2 = []
    for d, n in tr_ev.value_counts().sort_index().items():
        if n < 2:
            continue  # a day with 2+ separate truce posts (3 h apart) = a news cluster
        before = daily[(daily.index >= d - pd.Timedelta(days=3)) & (daily.index < d)].mean()
        after = daily[(daily.index > d) & (daily.index <= d + pd.Timedelta(days=3))].mean()
        h2.append(dict(day=d.date(), truce_posts=n, alert_h_per_unit_3d_before=before, after=after))
    h2 = pd.DataFrame(h2)
    h2.to_csv("results/events_h2_truce.csv", index=False)
    print("\nH2 — alert hours per unit per day, 3 days before vs after truce news clusters\n", h2.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
