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


# ---------- regressions from the 12 Oct audit ----------

def test_post_with_two_sections_real_615287():
    from airalerts.parse_signal import to_events
    text = ("🟡 Відбій червоної тривоги\nСумський район (Сумська обл.)\n\n"
            "🟢 Відбій тривоги\nСумський район (Сумська обл.)\nШосткинський район (Сумська обл.)")
    ev = to_events(posts((0, text)))
    assert ev[["unit", "kind"]].values.tolist() == [["Сумський район", "downgrade"], ["Сумський район", "end"],
                                                     ["Шосткинський район", "end"]]


def test_start_and_end_sections_in_one_post():
    from airalerts.parse_signal import to_events
    ev = to_events(posts((0, "🟡 Жовтий рівень тривоги\nЛубенський район (Полтавська обл.)\n\n🟢 Відбій тривоги\nКиїв")))
    assert dict(zip(ev.unit, ev.kind)) == {"Лубенський район": "start", "Київ": "end"}


def test_empty_episodes_have_schema():
    from airalerts.parse_signal import to_events
    ev = to_events(posts((0, "🟢 Відбій тривоги\n• Київ")))
    ep = to_episodes(ev, coverage_end=T0)
    assert ep.empty and "started_at" in ep.columns


def test_alert_starting_40s_after_issue_is_not_seen():
    from airalerts.data import Store, _mins
    s = pd.Series([pd.Timestamp("2026-09-10 04:00:40", tz="UTC")])
    e = pd.Series([pd.Timestamp("2026-09-10 05:00:00", tz="UTC")])
    st = Store({"A": _mins(s)}, {"A": _mins(e)}, {"A": "X"}, (T0, T0), None)
    assert st.active_until("A", pd.Timestamp("2026-09-10 04:00:00", tz="UTC")) is None
    assert st.active_until("A", pd.Timestamp("2026-09-10 04:00:41", tz="UTC")) is not None


def test_censored_rows_are_not_counted_as_cleared():
    from airalerts.dataset import clear_label
    df = pd.DataFrame(dict(remaining=[96.0, 96.0, 96.0, 300.0], event=[True, False, False, False],
                           capped=[False, True, False, False]))
    assert clear_label(df, 120).tolist() == [True, False, None, False] or \
        [None if pd.isna(x) else x for x in clear_label(df, 120)] == [True, False, None, False]


def test_alert_across_source_switch_survives(tmp_path):
    from airalerts import data as D
    sw = pd.Timestamp("2026-08-27 21:00", tz="UTC")
    off = pd.DataFrame(dict(oblast=["м. Київ"], raion=[None], hromada=[None], level=["oblast"],
                            started_at=["2026-08-27 21:00:10+00:00"], finished_at=["2026-08-27 23:00:00+00:00"],
                            source=["official"]))
    off.to_csv(tmp_path / "off.csv", index=False)
    tg = pd.DataFrame(dict(unit=["Київ"], oblast=["м. Київ"], unit_type=["kyiv"],
                           started_at=[pd.Timestamp("2026-08-27 20:59:50", tz="UTC")],
                           finished_at=[pd.Timestamp("2026-08-27 23:00:00", tz="UTC")], start_level=[None], max_level=[None],
                           n_level_changes=[0], levels=["[]"], censored=[False], source=["telegram"]))
    tg = pd.concat([tg.assign(started_at=pd.Timestamp("2026-08-20", tz="UTC"), finished_at=pd.Timestamp("2026-08-20 01:00", tz="UTC")), tg])
    tg.to_parquet(tmp_path / "tg.parquet")
    st = D.build_store(tmp_path / "off.csv", tmp_path / "tg.parquet", switch=sw)
    assert st.active_until("Київ", pd.Timestamp("2026-08-27 22:00", tz="UTC")) is not None
