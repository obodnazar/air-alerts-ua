"""Compute today's forecast and push it to the Cloudflare Worker (/ingest). Runs in GitHub Actions.

    INGEST_URL=https://.../ingest INGEST_SECRET=... PYTHONPATH=src python -m airalerts.botapp.publish

Decides by Kyiv local time what to publish (DST-safe, so the workflow can fire at two UTC times):
  20:00–21:30 -> evening forecast for the next school day;  08:00–09:30 -> the 08:00 update for today.
  19:30–20:00 and 07:30–08:00: the runner starts early (GitHub cron runs late by 5–20 min), waits for the issue time,
  then refreshes: the forecast still uses data up to 20:00 / 08:00 only, and lands ~2 min after it.
Anything else exits without work. In summer both UTC crons pass the window (20:05 and 21:05); the Worker keeps the
first forecast of a day (409 for the second), so every subscriber gets the same numbers.
Every run in a window, weekends included, also sends the hourly-risk parameters (kind "hourly", hourly.payload) and the
last 7 days of alerts per raion (kind "alerts"), which heal the Worker's own live scrape. Those two are best effort:
a failure is printed and never blocks the school forecast.

    --force evening 2026-10-13   publish now, whatever the time, and replace a forecast already in the Worker
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import os
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

MAIN = Path("data/raw/telegram/UkraineAlarmSignal.jsonl")
SNAPSHOT = Path("data/snapshot/UkraineAlarmSignal.jsonl.gz")


EARLY = {"19:30": "20:00", "07:30": "08:00"}  # start of an early window -> the issue time it waits for


def wait_for_issue(now: pd.Timestamp) -> pd.Timestamp:
    """Started early: sleep until 20 s past the issue time (the freshness guard needs data refreshed after it)."""
    hhmm = now.strftime("%H:%M")
    for start, issue in EARLY.items():
        if start <= hhmm < issue:
            target = now.normalize() + pd.Timedelta(hours=int(issue[:2]), seconds=20)
            print(f"{hhmm} Kyiv: waiting for {issue}", flush=True)
            time.sleep((target - now).total_seconds())
            return pd.Timestamp.now(tz="Europe/Kyiv")
    return now


def what_now(now: pd.Timestamp) -> tuple[str, str] | None:
    hhmm = now.strftime("%H:%M")
    # calendar days on the local date, not +24 h on a tz-aware time: on 25 Oct (25 h long) that gave Sunday again
    if "20:00" <= hhmm <= "21:30":
        kind, day = "evening", now.date() + dt.timedelta(days=1)
    elif "08:00" <= hhmm <= "09:30":
        kind, day = "morning", now.date()
    else:
        return None
    return (kind, day.isoformat()) if day.weekday() < 5 else None


def in_window(now: pd.Timestamp) -> bool:
    """Inside a publishing window, whatever the weekday (the hourly parameters are needed on weekends too)."""
    hhmm = now.strftime("%H:%M")
    return "20:00" <= hhmm <= "21:30" or "08:00" <= hhmm <= "09:30"


def forced(kind: str, day: str) -> tuple[str, str]:
    """Validate a manual --force; the workflow passes it through, so it is checked here, not trusted."""
    if kind not in ("evening", "morning"):
        raise SystemExit(f"--force: kind must be evening or morning, got {kind!r}")
    try:
        return kind, dt.date.fromisoformat(day).isoformat()
    except ValueError:
        raise SystemExit(f"--force: day must be YYYY-MM-DD, got {day!r}") from None


def seed_history() -> None:
    """A fresh runner has no scraped posts: start from the committed snapshot, then fetch only what is new."""
    if not MAIN.exists():
        MAIN.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(SNAPSHOT, "rb") as src, MAIN.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def clean(v):
    return None if isinstance(v, float) and math.isnan(v) else v


def newest_post() -> str | None:
    """Publish time of the newest alert post we hold: what the forecast really knows, not when it ran."""
    newest = None
    for f in (MAIN, MAIN.with_name("UkraineAlarmSignal_live.jsonl")):
        if f.exists():
            for line in f.open(encoding="utf-8"):
                if line.strip():
                    t = json.loads(line)["published_at"]
                    newest = t if newest is None or pd.Timestamp(t) > pd.Timestamp(newest) else newest
    return newest


def newest_post_id() -> int | None:
    ids = [json.loads(l)["id"] for f in (MAIN, MAIN.with_name("UkraineAlarmSignal_live.jsonl")) if f.exists()
           for l in f.open(encoding="utf-8") if l.strip()]
    return max(ids) if ids else None


def alert_history(units: set[str], data_end: str, max_id: int, days: int = 7) -> dict:
    """Alerts per raion that started in the last `days` before the newest post, as the Worker stores them:
    [unit, started ms, ended ms or None if still on, level now, highest level]."""
    eps = pd.read_parquet(Path("data/interim/telegram_episodes.parquet"))
    end = pd.Timestamp(data_end)
    since = end - pd.Timedelta(days, "D")
    e = eps[eps.unit.isin(units) & eps.unit_type.isin(["raion", "kyiv"]) & (eps.started_at >= since)]
    ms = lambda t: int(pd.Timestamp(t).value // 1_000_000)  # noqa: E731
    rows = [[r.unit, ms(r.started_at), None if r.censored else min(ms(r.finished_at), ms(end)),
             json.loads(r.levels)[-1][1] if r.levels else None, r.max_level] for r in e.itertuples()]
    return dict(kind="alerts", max_post_id=int(max_id), data_end=ms(end), since=ms(since), alerts=rows)


def extras(now: pd.Timestamp) -> list[dict]:
    """The two best-effort bodies: hourly parameters as of 00:00 Kyiv today, and the alert history."""
    from .. import hourly
    from ..data import build_store, eligible_units

    store = build_store(Path("data/raw/vadimkin/official_data_uk.csv"), Path("data/interim/telegram_episodes.parquet"))
    served = set(json.loads(Path("data/snapshot/bot_catalog.json").read_text(encoding="utf-8"))["units"])
    units = [u for u in eligible_units(store) if u in served]
    body = dict(kind="hourly", **hourly.payload(store, units, now.normalize().tz_localize(None)))
    return [body, alert_history(set(units), newest_post(), newest_post_id())]


def post(body: dict) -> tuple[int, str]:
    req = urllib.request.Request(os.environ["INGEST_URL"], data=json.dumps(body, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json",
                                          # Cloudflare rejects the default "Python-urllib" signature (error 1010)
                                          "User-Agent": "shkilnyi-ranok-publisher/1.0",
                                          "Authorization": f"Bearer {os.environ['INGEST_SECRET']}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode()[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


def main() -> None:
    from . import banner, forecasts
    from ..live import STAMP, refresh

    ap = argparse.ArgumentParser()
    ap.add_argument("--force", nargs=2, metavar=("KIND", "DAY"))
    a = ap.parse_args()
    now = pd.Timestamp.now(tz="Europe/Kyiv")
    if not a.force:
        now = wait_for_issue(now)
    job = forced(*a.force) if a.force else what_now(now)
    if not job and not in_window(now):
        print(f"{now:%H:%M} Kyiv: nothing to publish")
        return
    seed_history()
    refresh()
    failed = None
    if job:
        kind, day = job
        frame = forecasts.evening(day) if kind == "evening" else forecasts.morning(day)
        rows = [{k: clean(v) for k, v in r.items()} for r in frame.to_dict("records")]
        body = dict(kind=kind, day=day, rows=rows, banner=banner.fetch(pd.Timestamp.now(tz="UTC")), replace=bool(a.force),
                    data_end=newest_post(), checked_at=STAMP.read_text(encoding="utf-8").strip())
        status, text = post(body)
        if status == 409:
            print(kind, day, "-> already published earlier today; kept the first version")
        elif status != 200:
            failed = f"{kind} {day} -> {status} {text}"
        else:
            print(kind, day, "->", status, text)
    try:  # best effort, after the school forecast and whatever happened to it
        for body in extras(pd.Timestamp.now(tz="Europe/Kyiv")):
            print(body["kind"], "->", *post(body))
    except Exception as e:  # noqa: BLE001 - never block the school forecast on the hourly extras
        print("extras failed:", type(e).__name__, str(e)[:200])
    if failed:
        raise SystemExit(failed)


if __name__ == "__main__":
    main()
