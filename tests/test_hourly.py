"""Hourly risk model (PREREG item 12): formulas, limits, the bootstrap, and what the publisher sends.
Only the last test needs parsed data. Run: PYTHONPATH=src pytest -q"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

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


def test_publisher_windows_include_weekends():
    from airalerts.botapp.publish import in_window, what_now

    k = lambda s: pd.Timestamp(s, tz="Europe/Kyiv")  # noqa: E731
    assert what_now(k("2026-10-16 20:05")) is None and in_window(k("2026-10-16 20:05"))  # Friday: no school forecast,
    assert in_window(k("2026-10-18 08:10")) and not in_window(k("2026-10-18 12:00"))     # hourly parameters still go


@pytest.mark.skipif(not Path("data/interim/telegram_episodes.parquet").exists(), reason="data not parsed")
def test_alert_history_is_what_the_worker_accepts():
    from airalerts.botapp.publish import alert_history

    eps = pd.read_parquet("data/interim/telegram_episodes.parquet")
    end = eps.finished_at.max()
    b = alert_history({"Київ", "Харківський район"}, end.isoformat(), 1)
    assert b["alerts"] and {r[0] for r in b["alerts"]} <= {"Київ", "Харківський район"}
    for unit, started, ended, level, top in b["alerts"]:
        assert b["since"] <= started <= b["data_end"] and (ended is None or started <= ended <= b["data_end"])
        assert level in ("red", "yellow", None) and top in ("red", "yellow", None)


def test_training_rows_have_known_outcomes():
    """Version 2 (PREREG 13): day d learns only from earlier issue days, targets before d 00:00 and inside the data
    seen. Audit 7 removed the filter in a copy and every test stayed green; this one does not."""
    d = pd.Timestamp("2026-10-10")
    cut = H.kyiv_minute(d, 0)
    mk = lambda day, xs: pd.DataFrame(dict(day=pd.Timestamp(day), x=xs))  # noqa: E731
    pairs = {pd.Timestamp("2026-10-08"): mk("2026-10-08", [cut - 3000, cut + 60]),
             pd.Timestamp("2026-10-09"): mk("2026-10-09", [cut - 600, cut - 10, cut + 600]),
             d: mk("2026-10-10", [cut - 5])}                                # day d itself: never
    t = H.training_frame(pairs, d, cover_end=cut - 300)                       # data end 5 h before midnight
    assert (t.x < cut - 300).all() and (t.day < d).all()
    assert sorted(t.x - cut) == [-3000, -600]
    assert len(H.training_frame(pairs, d, cover_end=cut + 10_000)) == 3      # full data: all earlier targets < cut


def test_km_grid_all_censored():
    s = H.km_grid(np.array([1440.0]), np.array([False]))
    assert (s[: H.CAP] == 1).all() and (s[H.CAP:] == 0).all()
