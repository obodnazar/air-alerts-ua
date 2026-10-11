"""Turn @UkraineAlarmSignal posts into alert episodes per unit.

Post formats seen in Aug–Oct 2026 (see docs/research/01-data-landscape.md):

    🚨 <unit>\nПовітряна тривога. ...                  start, no level (before 4 Sep 2026)
    🟡 <unit>\nЖовтий рівень тривоги. ...              start or switch to yellow
    🔴 <unit>\nЧервоний рівень тривоги. ...            start or switch to red
    🟡 <unit>\nВідбій червоної тривоги, жовта ...      red -> yellow, alert continues
    🟢 <unit>\nВідбій тривоги. ...                     end
    🟡/🔴/🟢/🚨 <header>\n• <unit>\n• <unit> ...        the same for several units
    🛸 💣 🚀 💥 <city>\n<threat>                         city-level threat notice, no end post
    ⚠️ ... ЗМІ повідомляють про вибухи                  media report, ignored
    Мапа тривог станом на ...                          hourly snapshot, ignored here

A unit is "<name> (<oblast> обл.)" or "Київ". An episode runs from the first
start post to the all-clear; level changes inside it are kept as the max level.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

APOSTROPHES = str.maketrans({"ʼ": "'", "’": "'", "`": "'"})
UNIT_RE = re.compile(r"^(?:•\s*)?(?P<name>[^()\n•]+?)\s*(?:\((?P<oblast>[^)]+?)\s*обл\.\))?\s*$")
LEVEL_RANK = {None: 0, "yellow": 1, "red": 2}
HEADERS = {"відбій тривоги", "червоний рівень тривоги", "жовтий рівень тривоги", "повітряна тривога",
           "відбій червоної тривоги, жовта тривога ще триває"}


def norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.translate(APOSTROPHES)).strip()


def unit_type(name: str) -> str:
    if name == "Київ":
        return "kyiv"
    if name.endswith("район"):
        return "raion"
    if "громада" in name:
        return "hromada"
    return "city"


def classify(text: str) -> tuple[str, str | None]:
    """Return (kind, level) for a post; kind in start/end/downgrade/threat/ignore."""
    t = text.lower()
    if t.startswith("мапа тривог") or "змі повідомляють" in t:
        return "ignore", None
    if "відбій червоної тривоги" in t:
        return "downgrade", "yellow"
    if "відбій тривоги" in t:
        return "end", None
    if "червоний рівень" in t:
        return "start", "red"
    if "жовтий рівень" in t:
        return "start", "yellow"
    if "повітряна тривога" in t:
        return "start", None
    if "загроза" in t:
        return "threat", None
    return "ignore", None


def units_in(text: str) -> list[tuple[str, str | None]]:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return []
    first = re.sub(r"^[^\wА-Яа-яІіЇїЄєҐґ]+", "", lines[0]).strip()
    # Multi-unit posts start with a header line; the units follow, with or without "•"
    # (early version only read "•" lines and lost all-clears, leaving 24h+ "alerts").
    if first.lower() in HEADERS:
        candidates = [l for l in lines[1:] if UNIT_RE.match(l) and ("обл." in l or "Київ" in l)]
    else:
        candidates = [first]
    out = []
    for c in candidates:
        m = UNIT_RE.match(c)
        if not m:
            continue
        name = norm(m.group("name"))
        oblast = norm(m.group("oblast")) + " область" if m.group("oblast") else None
        if name == "Київ":
            oblast = "м. Київ"
        out.append((name, oblast))
    return out


@dataclass
class Open:
    start: pd.Timestamp
    level: str | None
    max_level: str | None
    start_post: int
    levels: list = field(default_factory=list)


def load_posts(path: Path) -> pd.DataFrame:
    rows = [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]
    df = pd.DataFrame(rows).drop_duplicates("id").sort_values("id")
    df["published_at"] = pd.to_datetime(df["published_at"], utc=True)
    return df


def to_events(posts: pd.DataFrame) -> pd.DataFrame:
    """One row per (post, unit): published_at, unit, oblast, unit_type, kind, level."""
    recs = []
    for p in posts.itertuples():
        kind, level = classify(p.text)
        if kind == "ignore":
            continue
        for name, oblast in units_in(p.text):
            recs.append(
                dict(post_id=p.id, published_at=p.published_at, unit=name, oblast=oblast,
                     unit_type=unit_type(name), kind=kind, level=level, edited=p.edited)
            )
    return pd.DataFrame(recs)


def to_episodes(events: pd.DataFrame, coverage_end: pd.Timestamp) -> pd.DataFrame:
    """Pair start/end events per unit into episodes. Open episodes at coverage_end are censored."""
    ev = events[events.kind.isin(["start", "end", "downgrade"])].sort_values(["published_at", "post_id"])
    open_: dict[tuple, Open] = {}
    out = []
    for e in ev.itertuples():
        key = (e.unit, e.oblast)
        cur = open_.get(key)
        if e.kind == "start":
            if cur is None:
                open_[key] = Open(e.published_at, e.level, e.level, e.post_id, [(e.published_at, e.level)])
            else:  # level change inside an ongoing alert
                cur.level = e.level
                cur.levels.append((e.published_at, e.level))
                if LEVEL_RANK[e.level] > LEVEL_RANK[cur.max_level]:
                    cur.max_level = e.level
        elif e.kind == "downgrade" and cur is not None:
            cur.level = "yellow"
            cur.levels.append((e.published_at, "yellow"))
        elif e.kind == "end" and cur is not None:
            out.append(_close(key, cur, e.published_at, censored=False, unit_type_=e.unit_type))
            del open_[key]
    for key, cur in open_.items():
        out.append(_close(key, cur, coverage_end, censored=True, unit_type_=unit_type(key[0])))
    return pd.DataFrame(out).sort_values("started_at").reset_index(drop=True)


def _close(key, cur: Open, end, censored: bool, unit_type_: str) -> dict:
    return dict(unit=key[0], oblast=key[1], unit_type=unit_type_, started_at=cur.start, finished_at=end,
                start_level=cur.levels[0][1], max_level=cur.max_level, n_level_changes=len(cur.levels) - 1,
                censored=censored, source="telegram:UkraineAlarmSignal")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Parse UkraineAlarmSignal posts into episodes")
    ap.add_argument("--posts", type=Path, default=Path("data/raw/telegram/UkraineAlarmSignal.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("data/interim/telegram_episodes.parquet"))
    args = ap.parse_args()
    posts = load_posts(args.posts)
    events = to_events(posts)
    eps = to_episodes(events, coverage_end=posts.published_at.max())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    events.to_parquet(args.out.with_name("telegram_events.parquet"))
    eps.to_parquet(args.out)
    print(f"posts={len(posts)} events={len(events)} episodes={len(eps)} "
          f"range={posts.published_at.min()} → {posts.published_at.max()}")
    print(eps.groupby("unit_type").size().to_string())


if __name__ == "__main__":
    main()
