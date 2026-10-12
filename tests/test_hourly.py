"""Hourly risk model (PREREG item 12): formulas, limits and the bootstrap. Needs no data. Run: PYTHONPATH=src pytest -q"""

from __future__ import annotations

import numpy as np
import pandas as pd

from airalerts import hourly as H
from airalerts import models as M


def toy(seed: int = 0, n: int = 3):
    rng = np.random.default_rng(seed)
    dur = rng.exponential(60, 400)
    surv = H.km_grid(np.minimum(dur, H.CAP * H.STEP), dur < H.CAP * H.STEP)
    par = dict(clim=rng.uniform(0, 0.9, (n, 24)), prof=rng.uniform(0.2, 2, (n, 24)), surv=surv)
    return par


def test_km_grid_matches_hand_count():
    s = H.km_grid(np.array([10.0, 20.0, 30.0, 40.0]), np.array([True, True, False, True]))
    # at 10: 3/4 left; at 20: 2/4; 30 censored; at 40: 0
    assert np.allclose(s[[0, 1, 2, 3, 4]], [1, 0.75, 0.5, 0.5, 0.0])
    assert (s[H.CAP:] == 0).all()


def test_limits_short_and_long_lead():
    par = toy()
    x = H.kyiv_minute(pd.Timestamp("2026-10-01"), 15)
    c = H.coverage_share(par["prof"], par["surv"], x)
    assert (np.diff(c, axis=1) >= -1e-12).all() and np.allclose(c[:, -1], 1) and (c[:, 0] == 0).all()
    clim = par["clim"][0, 15]
    assert H.forecast_one(clim, c[0], par["surv"], False, 0, 0) == 0          # no alert now, no time: none at x
    assert H.forecast_one(clim, c[0], par["surv"], True, 25, 0) == 1           # alert now: still on at lead 0
    assert abs(H.forecast_one(clim, c[0], par["surv"], False, 0, H.GRID) - clim) < 1e-12  # far ahead: climatology
    assert abs(H.forecast_one(clim, c[0], par["surv"], True, 25, H.GRID) - clim) < 1e-9
    assert H.forecast_one(clim, c[0], par["surv"], True, 25 * 60, 6) == 1      # past the 24 h cap: stuck, stays on


def test_vector_equals_scalar():
    par = toy(1, n=5)
    x = H.kyiv_minute(pd.Timestamp("2026-10-02"), 9)
    on = np.array([True, False, True, False, True])
    el = np.array([5.0, 0, 130, 0, 999])
    for steps in (1, 6, 37, 150, H.GRID):
        c = H.coverage_share(par["prof"], par["surv"], x)
        v = H.forecast(par, np.arange(5), on, el, x, steps)
        s = [H.forecast_one(par["clim"][i, 9], c[i], par["surv"], on[i], el[i], steps) for i in range(5)]
        assert np.allclose(v, s, atol=1e-12)
        assert ((v >= 0) & (v <= 1 + 1e-12)).all()


def test_buckets():
    assert [H.bucket_of(x) for x in (0.2, 1, 1.01, 2, 3, 4, 6, 7, 12, 13, 24, 25, 47)] == [0, 0, 1, 1, 2, 3, 3, 4, 4, 5, 5, 6, 6]


def test_bootstrap_on_sums_equals_row_bootstrap():
    rng = np.random.default_rng(3)
    days = np.repeat(pd.date_range("2026-09-15", periods=12), 40)
    y = rng.random(len(days)) < 0.3
    a, b = rng.random(len(days)), np.full(len(days), 0.3)
    ref = M.block_bootstrap_diff(y, a, b, days, n=200)
    daily = pd.DataFrame(dict(day=days, se_model=(a - y) ** 2, se_clim=(b - y) ** 2)).groupby("day", as_index=False).sum()
    got = H.bootstrap_sums(daily, n=200)
    assert np.allclose(ref, got, atol=1e-12)


def test_gates_follow_the_registered_rule():
    s = pd.DataFrame(dict(state=["on", "on", "off"], bucket=[0, 1, 0], ci_lo=[0.1, -0.01, 0.02],
                          bss_first_half=[0.2, 0.1, -0.01], bss_second_half=[0.2, 0.1, 0.05]))
    s["show_model"] = (s.ci_lo > 0) & (s.bss_first_half > 0) & (s.bss_second_half > 0)
    g = H.gates(s)
    assert g["on"][:2] == [True, False] and g["off"][0] is False and len(g["on"]) == len(H.BUCKETS)
