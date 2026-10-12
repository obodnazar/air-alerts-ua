"""Build model tables for M1 (alert at 08:00) and M2 (all-clear after 08:00).

Every feature is computed only from information available at the issue time T:
alert intervals are clipped at T, attack reports are filtered by their
publication time. `available_at` is stored per row and checked in tests.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .attacks import attack_features
from .data import KYIV_TZ, Store, _minute
from .parse_signal import level_at

ISSUES = {"20:00": -4 * 60, "07:00": -60}  # minutes relative to 08:00 of the target day


def kyiv(day: pd.Timestamp, hhmm: str) -> pd.Timestamp:
    h, m = map(int, hhmm.split(":"))
    return pd.Timestamp(day.year, day.month, day.day, h, m, tz=KYIV_TZ).tz_convert("UTC")


def weekdays(a: str, b: str) -> list[pd.Timestamp]:
    return [d for d in pd.date_range(a, b, freq="D") if d.weekday() < 5]


def state_matrix(store: Store, days: list[pd.Timestamp], hhmm: str) -> pd.DataFrame:
    return pd.DataFrame(
        {u: [store.active_until(u, kyiv(d, hhmm)) is not None for d in days] for u in store.units},
        index=pd.DatetimeIndex(days),
    )


def build_m1(store: Store, reports: pd.DataFrame, start: str, end: str, threats=None,
             evening: str = "20:00", neighbours: dict | None = None) -> pd.DataFrame:
    hist_start = (pd.Timestamp(start) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    alldays = list(pd.date_range(hist_start, end, freq="D"))
    y08 = state_matrix(store, alldays, "08:00")
    s20 = state_matrix(store, alldays, evening)
    s07 = state_matrix(store, alldays, "07:00")
    oblast_units = pd.Series(store.oblast_of)
    rows = []
    for d in weekdays(start, end):
        prev = d - pd.Timedelta(days=1)
        for issue, T in ((evening, kyiv(prev, evening)), ("07:00", kyiv(d, "07:00"))):
            state_now = s20.loc[prev] if issue == evening else s07.loc[d]
            country_share = state_now.mean()
            obl_share = state_now.groupby(oblast_units.reindex(state_now.index)).mean()
            atk = attack_features(reports, T)
            country_6h = np.mean([store.minutes_in(x, T - pd.Timedelta(hours=6), T) for x in store.units]) / 360
            # history strictly before the target day's 08:00, and before T
            hist = [x for x in alldays if x < d and x >= d - pd.Timedelta(days=42)]
            h28 = [x for x in hist if x >= d - pd.Timedelta(days=28)]
            for u in store.units:
                active = bool(state_now[u])
                last_end = store.last_end_before(u, T)
                # rolling-Markov: P(alert at 08:00 next | same state at the issue hour), 42 days
                if issue == evening:
                    pairs = [(bool(s20.at[x - pd.Timedelta(days=1), u]), bool(y08.at[x, u]))
                             for x in hist if x - pd.Timedelta(days=1) in s20.index]
                else:
                    pairs = [(bool(s07.at[x, u]), bool(y08.at[x, u])) for x in hist]
                same = [b for a, b in pairs if a == active]
                markov = (sum(same) + 1) / (len(same) + 2)
                rows.append(dict(
                    unit=u, oblast=store.oblast_of[u], day=d, issue=issue, issue_time=T,
                    y=bool(y08.at[d, u]),
                    active_now=active,
                    mins_since_end=0.0 if active else min(4320.0, (_minute(T) - last_end) if last_end else 4320.0),
                    unit_min_6h=store.minutes_in(u, T - pd.Timedelta(hours=6), T),
                    unit_min_24h=store.minutes_in(u, T - pd.Timedelta(hours=24), T),
                    unit_min_7d=store.minutes_in(u, T - pd.Timedelta(days=7), T),
                    night_min=store.minutes_in(u, kyiv(d, "00:00"), T) if issue == "07:00" else 0.0,
                    oblast_share_now=float(obl_share.get(store.oblast_of[u], 0.0)),
                    country_share_now=float(country_share),
                    clim28=float(y08.loc[h28, u].mean()) if h28 else np.nan,
                    markov42=markov,
                    dow=d.weekday(),
                    days_since_mass=atk["days_since_mass"], drones_7d=atk["drones_7d"], mass_30d=atk["mass_30d"],
                    country_6h=country_6h,
                    **neighbour_features(store, neighbours, u, state_now, T),
                    **threat_counts(threats, store.oblast_of[u], T),
                    available_at=max(T, atk["attack_available_at"]) if pd.notna(atk["attack_available_at"]) else T,
                ))
    return pd.DataFrame(rows)


def threat_counts(threats, oblast: str, T: pd.Timestamp) -> dict:
    """Air Force threat posts about the oblast in the last 1h/3h, and nationwide in the last 3h (before T)."""
    if threats is None:
        return dict(af_oblast_1h=np.nan, af_oblast_3h=np.nan, af_all_3h=np.nan)
    return dict(af_oblast_1h=threats.count(oblast, T, 60), af_oblast_3h=threats.count(oblast, T, 180),
                af_all_3h=threats.count_all(T, 180))


def _level_lookup(store: Store):
    tg = store.raw[store.raw.levels.notna()].sort_values("started_at")
    by_unit = {u: g for u, g in tg.groupby("unit")}

    def lookup(u: str, T: pd.Timestamp) -> str:
        g = by_unit.get(u)
        if g is None:
            return "unknown"
        hit = g[(g.started_at <= T) & (g.finished_at > T)]
        if hit.empty:
            return "unknown"
        return level_at(hit.iloc[-1].levels, T) or "unknown"

    return lookup


def build_m2(store: Store, reports: pd.DataFrame, start: str, end: str, all_days: bool = False, threats=None) -> pd.DataFrame:
    """One row per (unit, day) with an alert active at 08:00: time to all-clear from 08:00.
    `level` is the alert level in force at 08:00 (yellow/red, from 4 Sep 2026; else unknown)."""
    cov_end = _minute(store.coverage[1])
    level_of = _level_lookup(store)
    days = list(pd.date_range(start, end, freq="D")) if all_days else weekdays(start, end)
    rows = []
    for d in days:
        T = kyiv(d, "08:00")
        m = _minute(T)
        state = {u: store.active_until(u, T) for u in store.units}
        country_share = np.mean([v is not None for v in state.values()])
        atk = attack_features(reports, T)
        country_6h = np.mean([store.minutes_in(x, T - pd.Timedelta(hours=6), T) for x in store.units]) / 360
        for u, chain_end in state.items():
            if chain_end is None:
                continue
            s = store.starts[u]
            i = np.searchsorted(s, m, side="right") - 1
            elapsed = m - float(s[i])
            obl = [v is not None for k, v in state.items() if store.oblast_of[k] == store.oblast_of[u]]
            capped = (chain_end - float(s[i])) >= 24 * 60  # stuck alert cut at 24 h: it really went on longer
            censored = chain_end >= cov_end or capped
            rows.append(dict(
                unit=u, oblast=store.oblast_of[u], day=d, issue_time=T, level=level_of(u, T),
                remaining=float(min(chain_end, cov_end) - m), event=not censored, capped=capped,
                elapsed=float(elapsed),
                night_min=store.minutes_in(u, kyiv(d, "00:00"), T),
                unit_min_7d=store.minutes_in(u, T - pd.Timedelta(days=7), T),
                oblast_share_now=float(np.mean(obl)), country_share_now=float(country_share),
                dow=d.weekday(), days_since_mass=atk["days_since_mass"], drones_7d=atk["drones_7d"],
                country_6h=country_6h, **threat_counts(threats, store.oblast_of[u], T),
                available_at=T,
            ))
    cols = ["unit", "oblast", "day", "issue_time", "level", "remaining", "event", "capped", "elapsed", "night_min",
            "unit_min_7d", "oblast_share_now", "country_share_now", "dow", "days_since_mass", "drones_7d", "country_6h",
            "af_oblast_1h", "af_oblast_3h", "af_all_3h", "available_at"]
    df = pd.DataFrame(rows, columns=cols)
    for name, h in (("clear_by_10", 120), ("clear_by_11", 180)):
        df[name] = clear_label(df, h)
    return df


def clear_label(df: pd.DataFrame, horizon: float) -> pd.Series:
    """True/False if the all-clear within `horizon` minutes is known, NaN if not.
    Censored rows were once counted as cleared (audit, 12 Oct): a capped (24 h+) alert did not clear;
    an alert still running when the data end is unknown unless it already outlasted the horizon."""
    known = df.event | df.capped | (df.remaining > horizon)
    return (df.event & (df.remaining <= horizon)).astype(object).where(known, np.nan)


def neighbour_features(store: Store, neighbours: dict | None, u: str, state_now: pd.Series, T: pd.Timestamp) -> dict:
    """Share of adjacent raions under alert at T and their mean alert minutes in the last 6 h."""
    if not neighbours:
        return dict(nb_share_now=np.nan, nb_min_6h=np.nan)
    nb = [x for x in neighbours.get(u, []) if x in state_now.index]
    if not nb:
        return dict(nb_share_now=0.0, nb_min_6h=0.0)
    return dict(nb_share_now=float(state_now[nb].mean()),
                nb_min_6h=float(np.mean([store.minutes_in(x, T - pd.Timedelta(hours=6), T) for x in nb])))
