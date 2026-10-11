"""Daily Air Force attack reports (Kaggle: piterfm/massive-missile-attacks-on-ukraine).

A report covers a window (e.g. 18:00 → 09:00) and is published after the window
ends. We treat it as available at time_end + 2h (Kyiv time), never at time_start:
using a night's report to forecast that same night would be leakage.

A "mass" report-day: >= 300 drones or >= 20 missiles launched (as in
docs/research/05-military-and-miltech.md, where the monthly sums were checked
against ISIS tables).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

DRONE_KEYS = ("shahed", "geran", "uav", "gerbera", "orlan", "lancet", "zala", "supercam", "mohajer")
OK_SOURCES = ("kpszsu", "GeneralStaff.ua", "https:", "CinCAFU")
PUBLISH_LAG = pd.Timedelta(hours=2)


def load_reports(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df[df.source.astype(str).str.split("/").str[0].isin(OK_SOURCES)]
    df = df[df.time_start.astype(str).str.len() == 16]  # national report windows carry HH:MM
    df["time_end"] = pd.to_datetime(df.time_end, errors="coerce")
    df = df.dropna(subset=["time_end"])
    df["is_drone"] = df.model.astype(str).str.lower().apply(lambda m: any(k in m for k in DRONE_KEYS))
    df["launched"] = pd.to_numeric(df.launched, errors="coerce").fillna(0)
    df["available_at"] = (df.time_end.dt.tz_localize("Europe/Kyiv", ambiguous="NaT", nonexistent="shift_forward")
                          + PUBLISH_LAG).dt.tz_convert("UTC")
    day = df.groupby([df.time_end.dt.date, "is_drone"]).launched.sum().unstack(fill_value=0)
    day.columns = ["missiles" if not c else "drones" for c in day.columns]
    avail = df.groupby(df.time_end.dt.date).available_at.max()
    out = day.join(avail).reset_index(names="report_date")
    out["mass"] = (out.get("drones", 0) >= 300) | (out.get("missiles", 0) >= 20)
    return out.sort_values("available_at").reset_index(drop=True)


def known_at(reports: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    return reports[reports.available_at <= t]


def attack_features(reports: pd.DataFrame, t: pd.Timestamp) -> dict:
    k = known_at(reports, t)
    if k.empty:
        return dict(days_since_mass=60.0, drones_7d=0.0, mass_30d=0.0, attack_available_at=pd.NaT)
    last_mass = k[k.mass].available_at.max()
    recent = k[k.available_at > t - pd.Timedelta(days=7)]
    month = k[k.available_at > t - pd.Timedelta(days=30)]
    return dict(
        days_since_mass=min(60.0, (t - last_mass).total_seconds() / 86400) if pd.notna(last_mass) else 60.0,
        drones_7d=float(recent.get("drones", pd.Series(dtype=float)).sum()),
        mass_30d=float(month.mass.sum()),
        attack_available_at=k.available_at.max(),
    )
