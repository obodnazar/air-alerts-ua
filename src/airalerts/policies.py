"""Decision layer: what a school does each morning under the MoE rule, and what it costs.

MoE letter №1/19575 (11 Sep 2026): if an alert is active at the start of the day,
the day starts remotely; if the all-clear comes before the school's control
point, in-person lessons start after a ~60 min transition; otherwise the whole
day is remote.

Per (unit, day) we score:
  in_person  hours of in-person school between 08:00 and 15:00 (max 7)
  limbo      hours after 08:00 during which nobody knows how the day will go
Objective J = in_person - lam * limbo, lam is a stated preference, not an estimate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DAY_START, DAY_END, TRANSITION = 8.0, 15.0, 1.0


def react(alert: np.ndarray, clear_h: np.ndarray, cp: float) -> tuple[np.ndarray, np.ndarray]:
    """Status quo: wait until the control point. clear_h is the all-clear clock hour (Kyiv)."""
    ok = clear_h <= cp
    in_person = np.where(~alert, DAY_END - DAY_START, np.where(ok, np.maximum(0, DAY_END - (clear_h + TRANSITION)), 0.0))
    limbo = np.where(~alert, 0.0, np.minimum(clear_h, cp) - DAY_START)
    return in_person, limbo


def evaluate(days: pd.DataFrame, cp_by_unit: dict | None = None, tau_plan: float | None = None,
             tau_call: float | None = None, cp: float = 10.0, oracle: bool = False) -> pd.DataFrame:
    """days: unit, day, alert(bool), clear_h(float, NaN if no alert), p_remote_20 (M1×M2 at 20:00),
    p_clear (M2 at 08:00, NaN if no alert)."""
    d = days.copy()
    cps = d.unit.map(cp_by_unit).fillna(cp).to_numpy() if cp_by_unit else np.full(len(d), cp)
    alert = d.alert.to_numpy(bool)
    clear = d.clear_h.fillna(DAY_START).to_numpy(float)
    in_person, limbo = react(alert, clear, cps)
    remote_now = np.zeros(len(d), bool)
    if oracle:
        remote_now = alert & (clear > cps)
    else:
        if tau_plan is not None:  # decided the evening before: whole day remote, parents can plan
            remote_now |= d.p_remote_20.to_numpy(float) > tau_plan
        if tau_call is not None:  # decided at 08:00 when an alert is on
            remote_now |= alert & (d.p_clear.fillna(1).to_numpy(float) < tau_call)
    in_person = np.where(remote_now, 0.0, in_person)
    limbo = np.where(remote_now, 0.0, limbo)
    d["in_person"], d["limbo"] = in_person, limbo
    d["wrong_remote"] = remote_now & ~(alert & (clear > cps))  # called remote, but in-person was possible
    return d


def summarise(d: pd.DataFrame, lams=(0.0, 0.5, 1.0)) -> dict:
    out = dict(in_person_h=d.in_person.mean(), limbo_h=d.limbo.mean(), wrong_remote_share=d.wrong_remote.mean(),
               remote_days_share=(d.in_person == 0).mean())
    for lam in lams:
        out[f"J_lam{lam}"] = (d.in_person - lam * d.limbo).mean()
    return out


def best_control_point(days: pd.DataFrame, options=(9.0, 10.0, 11.0, 12.0), lam: float = 0.5) -> dict:
    """Per unit, the control point with the highest mean J on historical days (ties -> 10:00)."""
    best = {}
    for u, g in days.groupby("unit"):
        alert = g.alert.to_numpy(bool)
        clear = g.clear_h.fillna(DAY_START).to_numpy(float)
        js = {}
        for cp in options:
            ip, lb = react(alert, clear, np.full(len(g), cp))
            js[cp] = (ip - lam * lb).mean()
        top = max(js.values())
        best[u] = 10.0 if js[10.0] >= top - 1e-9 else max(js, key=js.get)
    return best
