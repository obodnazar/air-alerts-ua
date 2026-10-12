"""Tests that need no downloaded data: parser on real post formats, MoE rule, level timing, leakage guard.
Run: PYTHONPATH=src pytest -q"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from airalerts import models as M
from airalerts.parse_signal import classify, level_at, to_episodes, units_in
from airalerts.policies import react

T0 = pd.Timestamp("2026-09-17 00:00", tz="UTC")


def posts(*items):
    return pd.DataFrame([dict(id=i, published_at=T0 + pd.Timedelta(minutes=m), text=t, edited=False)
                         for i, (m, t) in enumerate(items)])


def test_formats_seen_in_the_channel():
    assert classify("🟡 Лубенський район (Полтавська обл.)\nЖовтий рівень тривоги. Прямуйте в укриття!") == ("start", "yellow")
    assert classify("🔴 Червоний рівень тривоги\nХарківський район (Харківська обл.)") == ("start", "red")
    assert classify("🚨 Київ\nПовітряна тривога. Прямуйте в укриття!") == ("start", None)
    assert classify("🟡 Миколаївський район (Миколаївська обл.)\nВідбій червоної тривоги, жовта тривога ще триває.")[0] == "downgrade"
    assert classify("🟢 Відбій тривоги\n• Київ") == ("end", None)
    assert classify("⚠️ Суми (Сумська обл.)\nЗМІ повідомляють про вибухи.")[0] == "ignore"
    assert classify("Мапа тривог станом на 23:00 – 11 жовтня:")[0] == "ignore"


def test_multi_unit_post_without_bullets():
    # this format lost its all-clears in the first parser version and produced fake 24h+ alerts
    text = ("🟢 Відбій тривоги\nХарківський район (Харківська обл.)\n"
            "Чугуївський район (Харківська обл.)\nБогодухівський район (Харківська обл.)")
    assert [u for u, _ in units_in(text)] == ["Харківський район", "Чугуївський район", "Богодухівський район"]


def test_apostrophes_and_kyiv_normalised():
    assert units_in("🟡 Куп’янський район (Харківська обл.)\nЖовтий рівень тривоги.") == [("Куп'янський район", "Харківська область")]
    assert units_in("🚨 Київ\nПовітряна тривога.") == [("Київ", "м. Київ")]


def test_episode_pairing_and_level_changes():
    from airalerts.parse_signal import to_events
    ev = to_events(posts(
        (0, "🟡 Богодухівський район (Харківська обл.)\nЖовтий рівень тривоги."),
        (30, "🔴 Червоний рівень тривоги\nБогодухівський район (Харківська обл.)"),
        (90, "🟢 Відбій тривоги\nБогодухівський район (Харківська обл.)"),
    ))
    ep = to_episodes(ev, coverage_end=T0 + pd.Timedelta(hours=3))
    assert len(ep) == 1
    e = ep.iloc[0]
    assert (e.finished_at - e.started_at) == pd.Timedelta(minutes=90)
    assert e.start_level == "yellow" and e.max_level == "red" and not e.censored
    # the level in force at a time, not the maximum: at +10 min it was still yellow
    assert level_at(e.levels, T0 + pd.Timedelta(minutes=10)) == "yellow"
    assert level_at(e.levels, T0 + pd.Timedelta(minutes=45)) == "red"


def test_open_alert_at_data_end_is_censored():
    from airalerts.parse_signal import to_events
    ev = to_events(posts((0, "🚨 Київ\nПовітряна тривога.")))
    ep = to_episodes(ev, coverage_end=T0 + pd.Timedelta(hours=2))
    assert ep.iloc[0].censored


def test_moe_rule():
    alert = np.array([False, True, True, True])
    clear = np.array([8.0, 9.0, 10.0, 10.5])  # all-clear clock hour
    in_person, limbo = react(alert, clear, np.full(4, 10.0))
    # no alert: 7h; clear 09:00 -> lessons from 10:00 (5h); clear exactly at 10:00 -> from 11:00 (4h); after -> remote
    assert in_person.tolist() == [7.0, 5.0, 4.0, 0.0]
    assert limbo.tolist() == [0.0, 1.0, 2.0, 2.0]


def test_guard_trips_on_a_perfect_score():
    y = np.array([0, 1] * 50)
    with pytest.raises(M.LeakageGuard):
        M.guard(y, y.astype(float), "planted")
    M.guard(y, np.full(100, 0.5), "honest")  # does not raise


def test_occupied_rule_from_map_matches_hand_list():
    from pathlib import Path

    from airalerts.data import EXCLUDED_OBLASTS, OCCUPIED  # noqa: F401

    share = Path("data/snapshot/occupied_share.csv")
    if not share.exists():
        pytest.skip("snapshot missing")
    occ = pd.read_csv(share)
    zoned_oblast_raions = {"Бердянський район", "Мелітопольський район", "Василівський район", "Пологівський район",
                           "Генічеський район", "Скадовський район", "Каховський район", "Херсонський район",
                           "Бериславський район", "Запорізький район", "Куп'янський район"}
    from_map = set(occ[(occ.occupied_share > 0.5) & occ.unit.isin(zoned_oblast_raions)].unit)
    assert from_map == OCCUPIED
