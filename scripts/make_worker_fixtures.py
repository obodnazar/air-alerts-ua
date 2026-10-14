"""Write the fixtures that keep the Worker's JS equal to the Python reference.

    PYTHONPATH=src python scripts/make_worker_fixtures.py [path/to/a/saved/t.me/s/page.html]

- signal_parity.json: 600 consecutive real posts (around the multi-section post 615287), with the events
  and the raion/Kyiv episodes Python's parse_signal makes from them.
- tme_page.html + tme_page.json: a saved t.me/s page and the posts Python's scraper parses from it.
- hourly_parity.json: published parameters for three raions and forecasts from hourly.forecast_one for many
  (time, target, state) cases, incl. the 25 Oct DST switch.
"""

from __future__ import annotations

import gzip
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from airalerts import hourly as H
from airalerts.data import build_store, eligible_units
from airalerts.parse_signal import to_episodes, to_events
from airalerts.sources.telegram import parse_page

OUT = Path("deploy/cloudflare/worker/test/fixtures")
MS = 60_000


def ms(t) -> int:
    return int(pd.Timestamp(t).value // 1_000_000)


def signal() -> None:
    rows = [json.loads(l) for l in gzip.open("data/snapshot/UkraineAlarmSignal.jsonl.gz", "rt", encoding="utf-8")]
    df = pd.DataFrame(rows).drop_duplicates("id").sort_values("id").reset_index(drop=True)
    i = int(df.index[df.id == 615287][0])
    df = df.iloc[i - 300:i + 300].copy()
    df["published_at"] = pd.to_datetime(df.published_at, utc=True)
    ev = to_events(df)
    eps = to_episodes(ev, coverage_end=df.published_at.max())
    eps = eps[eps.unit_type.isin(["raion", "kyiv"])]
    out = dict(
        posts=[dict(id=int(r.id), published_at=r.published_at.isoformat(), text=r.text) for r in df.itertuples()],
        events=[[int(e.post_id), e.unit, e.oblast, e.kind, e.level] for e in ev.itertuples()],
        episodes=[[e.unit, ms(e.started_at), None if e.censored else ms(e.finished_at), e.max_level,
                   json.loads(e.levels)[-1][1]] for e in eps.itertuples()],
    )
    (OUT / "signal_parity.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("signal:", len(out["posts"]), "posts,", len(out["events"]), "events,", len(out["episodes"]), "episodes")


def page(path: Path) -> None:
    shutil.copy(path, OUT / "tme_page.html")
    posts = parse_page(path.read_text(encoding="utf-8"), "UkraineAlarmSignal")
    (OUT / "tme_page.json").write_text(json.dumps([dict(id=p["id"], published_at=p["published_at"], text=p["text"])
                                                   for p in posts], ensure_ascii=False, indent=0), encoding="utf-8")
    print("page:", len(posts), "posts")


def hourly() -> None:
    store = build_store(Path("data/raw/vadimkin/official_data_uk.csv"), Path("data/interim/telegram_episodes.parquet"))
    units = eligible_units(store)
    pick = ["Київ", "Харківський район", "Львівський район"]
    body = H.payload(store, units, pd.Timestamp("2026-10-11"))
    body["units"] = {u: body["units"][u] for u in pick}
    surv = np.array(body["surv"])
    cases = []
    rng = np.random.default_rng(0)
    nows = [pd.Timestamp("2026-10-12 14:20", tz="Europe/Kyiv"), pd.Timestamp("2026-10-12 23:55", tz="Europe/Kyiv"),
            pd.Timestamp("2026-10-24 21:07", tz="Europe/Kyiv"), pd.Timestamp("2026-10-25 02:30", tz="Europe/Kyiv")]
    for now in nows:
        for u in pick:
            prof = np.array([body["units"][u]["prof"]])
            clim = np.array(body["units"][u]["clim"])
            for offset in (0, 1):
                day = (now + pd.Timedelta(offset, "D")).normalize().tz_localize(None)
                for hour in rng.choice(24, 6, replace=False):
                    x = H.kyiv_minute(day, int(hour))
                    lead = x - H.minute(now)
                    if lead <= 0:
                        continue
                    steps = int(lead // H.STEP)
                    c = H.coverage_share(prof, surv, x)[0]
                    hx = int(H.kyiv_hours(np.array([x]))[0])
                    for on, el in ((False, 0.0), (True, float(rng.integers(0, 300))), (True, 1500.0)):
                        p = H.forecast_one(clim[hx], c, surv, on, el, min(steps, H.GRID))
                        cases.append(dict(unit=u, now=ms(now), day=day.strftime("%Y-%m-%d"), hour=int(hour), on=on,
                                          elapsed=el, target=int(x * MS), local_hour=hx, steps=steps,
                                          bucket=H.bucket_of(lead / 60), p=p))
    (OUT / "hourly_parity.json").write_text(json.dumps(dict(params=body, cases=cases), ensure_ascii=False), encoding="utf-8")
    print("hourly:", len(cases), "cases")


def hourly_v2() -> None:
    """Version 2 (PREREG 13): coefficients, rows with their inputs and Python's predict_v2, and occupancy from raw
    alert intervals, so the Worker's forecastV2 and occupancy are checked against the reference."""
    store = build_store(Path("data/raw/vadimkin/official_data_uk.csv"), Path("data/interim/telegram_episodes.parquet"))
    units = eligible_units(store)
    day = pd.Timestamp("2026-10-11")
    coef = H.coef_asof(store, units, day)
    rows = H.day_pairs(store, units, day, H.minute(store.coverage[1])).sample(80, random_state=0).reset_index(drop=True)
    rows["p2"] = H.predict_v2(coef, rows)
    cases = [dict(on=bool(r.on), bucket=int(r.bucket), p1=float(r.p1), clim=float(r.clim), elapsed=float(r.elapsed),
                  occ6=float(r.occ6), occ24=float(r.occ24), oblast_on=float(r.oblast_on), country_on=float(r.country_on),
                  p2=float(r.p2)) for r in rows.itertuples()]
    occ = []
    for u in ["Київ", "Харківський район", "Сумський район"]:
        for T in (H.kyiv_minute(day, 9), H.kyiv_minute(day, 21) + 37):
            s, e = store.starts[u], store.ends[u]
            m = (s >= T - 48 * 60) & (s < T)
            iv = [dict(started=int(a * MS), ended=None if b > T else int(b * MS)) for a, b in zip(s[m], e[m])]
            v = H.occupancy(store, [u], T, 6)[0], H.occupancy(store, [u], T, 24)[0]
            occ.append(dict(unit=u, now=int(T * MS), rows=iv, occ6=float(v[0]), occ24=float(v[1])))
    (OUT / "hourly_v2_parity.json").write_text(json.dumps(dict(coef=coef, cases=cases, occupancy=occ), ensure_ascii=False),
                                              encoding="utf-8")
    print("hourly v2:", len(cases), "cases,", len(occ), "occupancy checks")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    signal()
    if len(sys.argv) > 1:
        page(Path(sys.argv[1]))
    hourly()
    hourly_v2()
