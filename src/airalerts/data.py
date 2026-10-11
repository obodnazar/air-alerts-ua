"""Load alert sources into one per-unit interval table, with coverage ends.

A *unit* is a raion ("Лубенський район", oblast "Полтавська область") or Kyiv
city ("Київ", oblast "м. Київ"). A unit is under alert when an alert covers the
raion itself or its whole oblast. Hromada/city-level alerts are sub-raion and do
not count for the unit (a school sits somewhere in the raion, not in every city).

Sources are stitched at SWITCH: official feed before it, Telegram after it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .parse_signal import norm

KYIV_TZ = "Europe/Kyiv"
SWITCH = pd.Timestamp("2026-09-01 00:00", tz=KYIV_TZ).tz_convert("UTC")
REGIME_START = pd.Timestamp("2025-12-15", tz="UTC")  # nationwide raion-level alerts
MAX_EPISODE = pd.Timedelta(hours=24)
# Raions renamed in 2024 (decommunisation law): the official feed keeps old names, Telegram uses new ones.
RENAMES = {"Красноградський район": "Берестинський район", "Володимир-Волинський район": "Володимирський район",
           "Червоноградський район": "Шептицький район"}
# Raions wholly or mostly under occupation: their alert series is identical to neighbours' and no schools
# there follow Ukrainian MoE rules. Donetsk oblast is excluded as a whole: it was left out of raion-level
# zoning (Dec 2025) and is under alert almost permanently. This is an assumption, stated in PREREG.md.
OCCUPIED = {"Бердянський район", "Мелітопольський район", "Василівський район", "Пологівський район",
            "Генічеський район", "Скадовський район", "Каховський район"}
EXCLUDED_OBLASTS = {"Донецька область", "Луганська область", "Автономна Республіка Крим"}  # longer "alerts" are stuck/frontline; treated as censored


def load_official(path: Path) -> tuple[pd.DataFrame, pd.Timestamp]:
    df = pd.read_csv(path)
    n = len(df)
    df = df.drop_duplicates()
    for c in ("started_at", "finished_at"):
        df[c] = pd.to_datetime(df[c], utc=True)
    df = df.dropna(subset=["started_at", "finished_at"])
    df = df[df.finished_at > df.started_at]
    coverage_end = df.started_at.max()
    df["oblast"] = df.oblast.map(norm)
    df["raion"] = df.raion.map(lambda x: RENAMES.get(norm(x), norm(x)) if isinstance(x, str) else None)
    print(f"official: {n} rows, {n - len(df)} dropped (duplicates/invalid), coverage end {coverage_end}")
    return df, coverage_end


def official_unit_alerts(df: pd.DataFrame) -> pd.DataFrame:
    """Rows: unit, oblast, scope ('unit' or 'oblast'), started_at, finished_at."""
    kyiv = df[df.oblast == "м. Київ"].assign(unit="Київ", scope="unit")
    rai = df[(df.level == "raion") & (df.oblast != "м. Київ")].assign(unit=lambda d: d.raion, scope="unit")
    obl = df[(df.level == "oblast") & (df.oblast != "м. Київ")].assign(unit=None, scope="oblast")
    out = pd.concat([kyiv, rai, obl])[["unit", "oblast", "scope", "started_at", "finished_at"]]
    out["censored"] = False
    out["max_level"] = None
    out["levels"] = None
    out["source"] = "official"
    return out


def telegram_unit_alerts(episodes: pd.DataFrame) -> pd.DataFrame:
    e = episodes[episodes.unit_type.isin(["raion", "kyiv"])].copy()
    e["scope"] = "unit"
    return e[["unit", "oblast", "scope", "started_at", "finished_at", "censored", "max_level", "levels", "source"]]


@dataclass
class Store:
    """Merged alert intervals per unit, in integer minutes since epoch."""

    starts: dict
    ends: dict
    oblast_of: dict
    coverage: tuple  # (start, end) UTC of usable data
    raw: pd.DataFrame

    @property
    def units(self) -> list:
        return sorted(self.starts)

    def active_until(self, unit: str, t: pd.Timestamp) -> int | None:
        """If the unit is under alert at t, return the end minute of the merged chain."""
        m = _minute(t)
        s, e = self.starts[unit], self.ends[unit]
        i = np.searchsorted(s, m, side="right") - 1
        if i >= 0 and e[i] > m:
            return int(e[i])
        return None

    def minutes_in(self, unit: str, a: pd.Timestamp, b: pd.Timestamp) -> float:
        lo, hi = _minute(a), _minute(b)
        s, e = self.starts[unit], self.ends[unit]
        return float(np.clip(np.minimum(e, hi) - np.maximum(s, lo), 0, None).sum())

    def last_end_before(self, unit: str, t: pd.Timestamp) -> int | None:
        m = _minute(t)
        e = self.ends[unit]
        e = e[e <= m]
        return int(e.max()) if len(e) else None


def _minute(t: pd.Timestamp) -> int:
    return int(pd.Timestamp(t).tz_convert("UTC").value // 60_000_000_000)


def _mins(series: pd.Series) -> np.ndarray:
    if series.empty:
        return np.array([], dtype=np.int64)
    return (series.dt.tz_convert("UTC").dt.as_unit("ns").astype("int64") // 60_000_000_000).to_numpy()


def _merge(starts: np.ndarray, ends: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(starts)
    s, e = starts[order], ends[order]
    out_s, out_e = [], []
    for a, b in zip(s, e):
        if out_e and a <= out_e[-1]:
            out_e[-1] = max(out_e[-1], b)
        else:
            out_s.append(a)
            out_e.append(b)
    return np.array(out_s, dtype=np.int64), np.array(out_e, dtype=np.int64)


def build_store(official_csv: Path, telegram_episodes: Path, switch: pd.Timestamp = SWITCH) -> Store:
    off, off_end = load_official(official_csv)
    off = off[off.started_at >= REGIME_START - pd.Timedelta(days=60)]
    tg = pd.read_parquet(telegram_episodes)
    tg_start = tg.started_at.min() + pd.Timedelta(hours=24)  # warm-up: alerts open at scrape start are unseen
    tg_end = tg.finished_at.max()
    if tg_start > switch:
        print(f"WARNING: Telegram data starts {tg_start}, after the switch {switch}: gap in coverage")
    a = official_unit_alerts(off)
    a = a[a.started_at < switch]
    b = telegram_unit_alerts(tg)
    b = b[b.started_at >= switch]
    allr = pd.concat([a, b], ignore_index=True)
    # cap stuck alerts: they are censored at 24h rather than counted as a day-long alert
    long = (allr.finished_at - allr.started_at) > MAX_EPISODE
    allr.loc[long, "censored"] = True
    allr.loc[long, "finished_at"] = allr.loc[long, "started_at"] + MAX_EPISODE

    oblast_of = {}
    for u, o in allr[allr.scope == "unit"][["unit", "oblast"]].drop_duplicates().itertuples(index=False):
        oblast_of[u] = o
    starts, ends = {}, {}
    obl_rows = allr[allr.scope == "oblast"]
    for u, o in oblast_of.items():
        own = allr[(allr.scope == "unit") & (allr.unit == u)]
        cov = obl_rows[obl_rows.oblast == o]
        s = np.concatenate([_mins(own.started_at), _mins(cov.started_at)])
        e = np.concatenate([_mins(own.finished_at), _mins(cov.finished_at)])
        starts[u], ends[u] = _merge(s, e)
    coverage = (REGIME_START, tg_end)
    store_gap = (switch, tg_start) if tg_start > switch else None
    print(f"store: {len(starts)} units, coverage {coverage[0]} → {coverage[1]}, switch {switch}")
    st = Store(starts, ends, oblast_of, coverage, allr)
    st.gap = store_gap
    return st


def compare_sources(official_csv: Path, telegram_episodes: Path, a: str, b: str, tol_min: int = 3) -> pd.DataFrame:
    """Share of official raion/Kyiv alert starts matched by a Telegram start within ±tol_min, per window."""
    off, _ = load_official(official_csv)
    o = official_unit_alerts(off)
    o = o[(o.scope == "unit") & (o.started_at >= a) & (o.started_at < b)]
    t = telegram_unit_alerts(pd.read_parquet(telegram_episodes))
    t = t[(t.started_at >= a) & (t.started_at < b)]
    rows = []
    for name, x, y in (("official→telegram", o, t), ("telegram→official", t, o)):
        hit = 0
        for u, g in x.groupby("unit"):
            ys = np.sort(_mins(y[y.unit == u].started_at))
            for m in _mins(g.started_at):
                j = np.searchsorted(ys, m)
                near = [abs(ys[k] - m) for k in (j - 1, j) if 0 <= k < len(ys)]
                hit += bool(near) and min(near) <= tol_min
        rows.append(dict(direction=name, n=len(x), matched=hit, share=hit / max(len(x), 1)))
    return pd.DataFrame(rows)


def eligible_units(store: Store, until: pd.Timestamp = SWITCH) -> list[str]:
    """Units with at least one alert of their own in the official feed since the raion-level regime.

    Occupied raions only ever get oblast-wide alerts in the official feed and are
    dropped (Telegram lists them by name, so they must be filtered here).
    """
    r = store.raw
    own = r[(r.source == "official") & (r.scope == "unit") & (r.started_at >= REGIME_START) & (r.started_at < until)]
    keep = set(own.unit.dropna()) - OCCUPIED
    return [u for u in store.units if u in keep and store.oblast_of[u] not in EXCLUDED_OBLASTS]
