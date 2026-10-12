"""Compute today's forecast and push it to the Cloudflare Worker (/ingest). Runs in GitHub Actions.

    INGEST_URL=https://.../ingest INGEST_SECRET=... PYTHONPATH=src python -m airalerts.botapp.publish

Decides by Kyiv local time what to publish (DST-safe, so the workflow can fire at two UTC times):
  20:00–21:30 -> evening forecast for the next school day;  08:00–09:30 -> the 08:00 update for today.
Anything else, or a weekend target day, exits without work.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import shutil
import sys
import urllib.request
from pathlib import Path

import pandas as pd

MAIN = Path("data/raw/telegram/UkraineAlarmSignal.jsonl")
SNAPSHOT = Path("data/snapshot/UkraineAlarmSignal.jsonl.gz")


def what_now(now: pd.Timestamp) -> tuple[str, str] | None:
    hhmm = now.strftime("%H:%M")
    if "20:00" <= hhmm <= "21:30":
        kind, day = "evening", now.normalize() + pd.Timedelta(days=1)
    elif "08:00" <= hhmm <= "09:30":
        kind, day = "morning", now.normalize()
    else:
        return None
    return (kind, f"{day:%Y-%m-%d}") if day.weekday() < 5 else None


def seed_history() -> None:
    """A fresh runner has no scraped posts: start from the committed snapshot, then fetch only what is new."""
    if not MAIN.exists():
        MAIN.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(SNAPSHOT, "rb") as src, MAIN.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def clean(v):
    return None if isinstance(v, float) and math.isnan(v) else v


def main() -> None:
    from . import banner, forecasts
    from ..live import refresh

    now = pd.Timestamp.now(tz="Europe/Kyiv")
    job = what_now(now) if "--force" not in sys.argv else (sys.argv[sys.argv.index("--force") + 1], sys.argv[sys.argv.index("--force") + 2])
    if not job:
        print(f"{now:%H:%M} Kyiv: nothing to publish")
        return
    kind, day = job
    seed_history()
    refresh()
    frame = forecasts.evening(day) if kind == "evening" else forecasts.morning(day)
    rows = [{k: clean(v) for k, v in r.items()} for r in frame.to_dict("records")]
    body = dict(kind=kind, day=day, rows=rows, banner=banner.fetch(pd.Timestamp.now(tz="UTC")),
                data_end=str(pd.Timestamp.now(tz="UTC")))
    req = urllib.request.Request(os.environ["INGEST_URL"], data=json.dumps(body, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {os.environ['INGEST_SECRET']}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        print(kind, day, "->", r.status, r.read().decode()[:200])


if __name__ == "__main__":
    main()
