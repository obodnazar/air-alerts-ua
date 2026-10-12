"""Live run for a real school morning: forecast before it happens, check after.

    PYTHONPATH=src python -m airalerts.live forecast --day 2026-10-12 --issue 20:00
    PYTHONPATH=src python -m airalerts.live forecast --day 2026-10-12 --issue 07:00
    PYTHONPATH=src python -m airalerts.live check --day 2026-10-12

Forecast files are committed to git before 08:00, so the commit time proves they
were made before the outcome was known.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .brief import make_brief, render
from .data import build_store
from .dataset import kyiv

LIVE = Path("results/live")
MAIN = Path("data/raw/telegram/UkraineAlarmSignal.jsonl")
TOPUP = Path("data/raw/telegram/UkraineAlarmSignal_live.jsonl")
STAMP = Path("data/interim/last_refresh")


def refresh() -> None:
    """Fetch posts newer than anything saved, into a separate top-up file, then re-parse."""
    if not TOPUP.exists():
        newest = max((json.loads(l) for l in MAIN.open(encoding="utf-8") if l.strip()), key=lambda r: r["id"])
        TOPUP.write_text(json.dumps(newest, ensure_ascii=False) + "\n", encoding="utf-8")
    py = sys.executable
    subprocess.run([py, "-m", "airalerts.sources.telegram", "UkraineAlarmSignal", "--update", "--out", str(TOPUP)], check=True)
    subprocess.run([py, "-m", "airalerts.parse_signal"], check=True)
    subprocess.run([py, "-m", "airalerts.sources.kaggle"], check=False)  # best effort: last known reports stay
    STAMP.write_text(pd.Timestamp.now(tz="UTC").isoformat(), encoding="utf-8")  # proof the data reach "now"


def forecast(day: str, issue: str) -> None:
    refresh()
    b = make_brief(day, issue=issue)
    LIVE.mkdir(parents=True, exist_ok=True)
    tag = f"{day}_{issue.replace(':', '')}"
    b.to_csv(LIVE / f"forecast_{tag}.csv", index=False)
    (LIVE / f"brief_{tag}.md").write_text(render(b), encoding="utf-8")
    print(render(b))


def check(day: str) -> None:
    refresh()
    store = build_store(Path("data/raw/vadimkin/official_data_uk.csv"), Path("data/interim/telegram_episodes.parquet"))
    d = pd.Timestamp(day)
    t8, t10 = kyiv(d, "08:00"), kyiv(d, "10:00")
    have10 = store.coverage[1] >= t10
    rows = []
    for f in sorted(LIVE.glob(f"forecast_{day}_*.csv")):
        fc = pd.read_csv(f)
        issue = f.stem.split("_")[-1]
        for r in fc.itertuples():
            end = store.active_until(r.unit, t8)
            alert8 = end is not None
            clear10 = (end <= int(t10.value // 60_000_000_000)) if (alert8 and have10) else np.nan
            rows.append(dict(issue=issue, unit=r.unit, p_alert_08=r.p_alert_08, usual=r.usual,
                             p_clear_by_10=r.p_clear_by_10, alert_08=alert8, clear_by_10=clear10))
    res = pd.DataFrame(rows)
    lines = [f"# Live check · {day}", f"Data up to {store.coverage[1].tz_convert('Europe/Kyiv'):%d.%m %H:%M} Kyiv.", ""]
    for issue, g in res.groupby("issue"):
        y = g.alert_08.astype(float)
        bm, bc = ((g.p_alert_08 - y) ** 2).mean(), ((g.usual - y) ** 2).mean()
        lines.append(f"## Forecast issued {issue[:2]}:{issue[2:]}: alert at 08:00")
        lines.append(f"- units: {len(g)}, alert at 08:00 in {int(y.sum())} ({y.mean():.0%}); mean forecast {g.p_alert_08.mean():.0%}")
        lines.append(f"- Brier model {bm:.4f} vs climatology {bc:.4f} (BSS {1 - bm / bc:+.3f}) — one morning, so noisy")
        hit = g[g.alert_08]
        if len(hit) and have10:
            c = hit.clear_by_10.astype(float)
            lines.append(f"- of the {len(hit)} alerts at 08:00, all-clear by 10:00: {int(c.sum())} ({c.mean():.0%}); "
                         f"forecast said {hit.p_clear_by_10.mean():.0%} on average; Brier {((hit.p_clear_by_10 - c) ** 2).mean():.4f}")
        top = g.sort_values("p_alert_08", ascending=False).head(10)
        lines.append("\n| unit | P(alert 08) | alert? | P(clear≤10) | cleared? |\n|---|---|---|---|---|")
        for r in top.itertuples():
            lines.append(f"| {r.unit} | {r.p_alert_08:.0%} | {'так' if r.alert_08 else 'ні'} | {r.p_clear_by_10:.0%} | "
                         f"{'—' if pd.isna(r.clear_by_10) else ('так' if r.clear_by_10 else 'ні')} |")
        lines.append("")
    out = LIVE / f"check_{day}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    res.to_csv(LIVE / f"check_{day}.csv", index=False)
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["forecast", "check"])
    ap.add_argument("--day", required=True)
    ap.add_argument("--issue", default="20:00")
    a = ap.parse_args()
    forecast(a.day, a.issue) if a.cmd == "forecast" else check(a.day)


if __name__ == "__main__":
    main()
