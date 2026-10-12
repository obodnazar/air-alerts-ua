"""Leakage controls registered in PREREG.md. Run: PYTHONPATH=src pytest -q"""

from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from airalerts import models as M
from airalerts.attacks import load_reports
from airalerts.data import _minute, build_store
from airalerts.dataset import build_m1, build_m2, kyiv

OFFICIAL = Path("data/raw/vadimkin/official_data_uk.csv")
TELEGRAM = Path("data/interim/telegram_episodes.parquet")
KAGGLE = Path("data/raw/kaggle/missile_attacks_daily.csv")
pytestmark = pytest.mark.skipif(not (OFFICIAL.exists() and TELEGRAM.exists() and KAGGLE.exists()),
                                reason="data not downloaded")
FEATURES = [c for c in M.M1_FEATURES]


@pytest.fixture(scope="module")
def world():
    store = build_store(OFFICIAL, TELEGRAM)
    reports = load_reports(KAGGLE)
    return store, reports


def truncate(store, reports, T):
    """What the world looked like at T: alerts that started after T are unknown, ongoing ones are open."""
    st = copy.copy(store)
    m = _minute(T)
    st.starts, st.ends = {}, {}
    for u in store.units:
        s, e = store.starts[u], store.ends[u]
        keep = s <= m
        st.starts[u] = s[keep]
        st.ends[u] = np.where(e[keep] > m, m + 1 / 60, e[keep])  # still running at T: end unknown
    st.raw = store.raw[store.raw.started_at <= T].assign(finished_at=lambda x: x.finished_at.clip(upper=T + pd.Timedelta(seconds=1)))
    return st, reports[reports.available_at <= T]


def test_available_at_never_after_issue(world):
    store, reports = world
    m1 = build_m1(store, reports, "2026-08-03", "2026-08-14")
    assert (m1.available_at <= m1.issue_time).all()
    m2 = build_m2(store, reports, "2026-08-03", "2026-08-14")
    assert (m2.available_at <= m2.issue_time).all()


M1_ALL = FEATURES + ["country_6h"]
M2_ALL = ["elapsed", "night_min", "unit_min_7d", "oblast_share_now", "country_share_now", "days_since_mass", "drones_7d",
          "country_6h", "level"]


def m1_at(store, reports, day, issue):
    m = build_m1(store, reports, day, day)
    return m[m.issue == issue].set_index("unit")[M1_ALL].sort_index()


def m2_at(store, reports, day):
    m = build_m2(store, reports, day, day, all_days=True)
    return m.set_index("unit")[M2_ALL].sort_index()


@pytest.mark.parametrize("day", ["2026-05-13", "2026-08-12", "2026-09-23"])
@pytest.mark.parametrize("issue", ["20:00", "07:00", "08:00"])
def test_features_equal_on_truncated_world(world, day, issue):
    """Every feature column, at every issue time, from a world cut at that time (audit: the first version
    checked only the base 20:00 features)."""
    store, reports = world
    d = pd.Timestamp(day)
    T = kyiv(d - pd.Timedelta(days=1), "20:00") if issue == "20:00" else kyiv(d, issue)
    st, rep = truncate(store, reports, T)
    if issue == "08:00":
        full, cut = m2_at(store, reports, day), m2_at(st, rep, day)
    else:
        full, cut = m1_at(store, reports, day, issue), m1_at(st, rep, day, issue)
    pd.testing.assert_frame_equal(full, cut, check_dtype=False)


def test_truncation_check_catches_a_one_hour_peek(world, monkeypatch):
    """Mutation test: make 'minutes in the last 6 h' peek one hour ahead; the check must now fail."""
    from airalerts import data as D

    store, reports = world
    day, issue = "2026-09-23", "07:00"
    T = kyiv(pd.Timestamp(day), issue)
    orig = D.Store.minutes_in
    monkeypatch.setattr(D.Store, "minutes_in", lambda self, u, a, b: orig(self, u, a, b + pd.Timedelta(hours=1)))
    st, rep = truncate(store, reports, T)
    with pytest.raises(AssertionError):
        pd.testing.assert_frame_equal(m1_at(store, reports, day, issue), m1_at(st, rep, day, issue), check_dtype=False)


def test_planted_leak_trips_guard(world):
    store, reports = world
    df = build_m1(store, reports, "2026-07-06", "2026-08-14")
    df = df[df.issue == "20:00"]
    train, test = df[df.day < "2026-08-01"], df[df.day >= "2026-08-01"]
    leaky = FEATURES + ["y_leak"]
    fit = M.fit_m1(train.assign(y_leak=train.y.astype(float)), features=leaky)
    p = M.predict_m1(fit, test.assign(y_leak=test.y.astype(float)))["logit"]
    with pytest.raises(M.LeakageGuard):
        M.guard(test.y, p, "planted leak")


def test_attack_reports_not_available_before_window_ends(world):
    _, reports = world
    end_local = pd.to_datetime(reports.report_date).dt.tz_localize("Europe/Kyiv")
    assert (reports.available_at >= end_local.dt.tz_convert("UTC")).all()


def test_truncation_check_bites_on_future_feature(world):
    """Sanity check of the check itself: a feature that peeks 12h ahead must differ on the truncated world."""
    store, reports = world
    T = kyiv(pd.Timestamp("2026-08-11"), "20:00")
    st, _ = truncate(store, reports, T)
    ahead = pd.Timedelta(hours=12)
    full = [store.minutes_in(u, T, T + ahead) for u in store.units]
    cut = [st.minutes_in(u, T, T + ahead) for u in store.units]
    assert full != cut


AF = Path("data/raw/telegram/kpszsu.jsonl")


@pytest.mark.skipif(not AF.exists(), reason="Air Force posts not scraped")
def test_airforce_counts_use_only_posts_before_issue():
    from airalerts.airforce import ThreatIndex, load_threat_posts

    posts = load_threat_posts(AF)
    T = posts.published_at.quantile(0.5)
    full, cut = ThreatIndex(posts), ThreatIndex(posts[posts.published_at < T])
    for ob in posts.oblast.unique()[:10]:
        for minutes in (60, 180):
            assert full.count(ob, T, minutes) == cut.count(ob, T, minutes)
    assert full.count_all(T, 180) == cut.count_all(T, 180)
