"""The bot without Telegram: a fake transport records what would be sent."""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pandas as pd
import pytest

from airalerts.botapp import banner, catalog, ui
from airalerts.botapp.main import broadcast, due
from airalerts.botapp.router import Router
from airalerts.botapp.store import Store
from airalerts.botapp.telegram import Blocked, Telegram

pytestmark = pytest.mark.skipif(not catalog.CATALOG.exists(), reason="catalog snapshot missing")


class FakeTG:
    def __init__(self, blocked=()):
        self.calls, self.blocked = [], set(blocked)

    def send(self, chat, text, keyboard=None):
        if chat in self.blocked:
            raise Blocked("blocked")
        self.calls.append(("send", chat, text, keyboard))

    def edit(self, chat, mid, text, keyboard=None):
        self.calls.append(("edit", chat, text, keyboard))

    def answer(self, cid, text=None):
        self.calls.append(("answer", cid))


@pytest.fixture
def bot():
    cat, tg, st = catalog.load(), FakeTG(), Store(":memory:")
    return Router(st, cat, tg, lambda u, cp: f"now {u} {cp}"), tg, st, cat


def msg(text, chat=1):
    return {"message": {"chat": {"id": chat}, "text": text}}


def cb(data, chat=1):
    return {"callback_query": {"id": "q", "data": data, "message": {"chat": {"id": chat}, "message_id": 5}}}


def buttons(keyboard):
    return [b for row in keyboard["inline_keyboard"] for b in row]


def test_start_shows_oblasts_and_buttons_fit_telegram_limit(bot):
    r, tg, _, cat = bot
    r.handle(msg("/start"))
    kb = tg.calls[-1][3]
    assert len(buttons(kb)) == len(cat["oblasts"])
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons(kb))


def test_every_raion_reachable_through_buttons(bot):
    r, tg, st, cat = bot
    reached = set()
    for i, _ in enumerate(cat["oblasts"]):
        r.handle(cb(f"ob:{i}"))
        reached |= {b["callback_data"] for b in buttons(tg.calls[-2][3]) if b["callback_data"].startswith("u:")}
    assert len(reached) == len(cat["units"]) == 104


def test_subscribe_and_settings(bot):
    r, tg, st, cat = bot
    j = cat["units"].index("Бучанський район")
    r.handle(cb(f"u:{j}"))
    assert st.get(1)["unit"] == "Бучанський район" and st.get(1)["cp"] == 10
    r.handle(cb("cp:11"))
    r.handle(cb("m:always"))
    assert st.get(1)["cp"] == 11 and st.get(1)["morning"] == "always"
    r.handle(msg("/stop"))
    assert st.active() == []


@pytest.mark.parametrize("query,expected", [("Біла Церква", "Білоцерківський район"), ("Ірпінь", "Бучанський район"),
                                            ("Кривий Ріг", "Криворізький район"), ("Київ", "Київ"), ("Одеса", "Одеський район")])
def test_town_search(bot, query, expected):
    r, tg, _, cat = bot
    r.handle(msg(query))
    first = buttons(tg.calls[-1][3])[0]["callback_data"]
    assert cat["units"][int(first.split(":")[1])] == expected


def test_occupied_town_is_not_offered(bot):
    r, tg, _, _ = bot
    r.handle(msg("Мелітополь"))
    assert "не обслуговується" in tg.calls[-1][2]


def frame():
    return pd.DataFrame([dict(unit=u, p_alert_08=0.4, usual=0.3, **{f"p_clear_{c}": 0.2 + 0.1 * (c - 9) for c in (9, 10, 11, 12)})
                         for u in ("Київ", "Бучанський район", "Харківський район")])


def test_evening_broadcast_is_idempotent_and_skips_blocked():
    st, tg = Store(":memory:"), FakeTG(blocked={3})
    for chat, unit in ((1, "Київ"), (2, "Бучанський район"), (3, "Харківський район")):
        st.subscribe(chat, unit)
    st.set(2, "cp", 12)
    day = pd.Timestamp("2026-10-13")
    assert broadcast("evening", day, st, tg, frame(), "", pause=0) == 2
    assert "до 12:00: 50%" in [c for c in tg.calls if c[1] == 2][0][2]  # uses the subscriber's control point
    assert st.get(3)["active"] == 0  # blocked user switched off
    st.kv("done:evening:2026-10-13", None or "")  # allow a re-run attempt
    assert broadcast("evening", day, st, tg, frame(), "", pause=0) == 0  # nothing sent twice


def test_morning_only_alert_unless_always():
    st, tg = Store(":memory:"), FakeTG()
    st.subscribe(1, "Київ")
    st.subscribe(2, "Бучанський район")
    st.set(2, "morning", "always")
    m = pd.DataFrame([dict(unit="Київ", level="red", elapsed_min=130, p_clear_9=0.1, p_clear_10=0.2, p_clear_11=0.3, p_clear_12=0.4)])
    broadcast("morning", pd.Timestamp("2026-10-13"), st, tg, m, "", pause=0)
    texts = {c[1]: c[2] for c in tg.calls}
    assert "оголосіть дистанційний день" in texts[1] and "Тривоги немає" in texts[2]


@pytest.mark.parametrize("now,kind,expected", [
    ("2026-10-11 20:00", "evening", "2026-10-12"),  # Sunday evening -> Monday
    ("2026-10-16 20:00", "evening", None),          # Friday evening -> Saturday: no school
    ("2026-10-12 08:03", "morning", "2026-10-12"),
    ("2026-10-12 12:00", "morning", None),          # outside the window
])
def test_schedule(now, kind, expected):
    d = due(kind, pd.Timestamp(now, tz="Europe/Kyiv"), Store(":memory:"))
    assert (f"{d:%Y-%m-%d}" if d is not None else None) == expected


def test_retry_on_429():
    calls, slept = [], []

    def opener(url, data=None, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(url, 429, "Too Many", {}, io.BytesIO(json.dumps({"parameters": {"retry_after": 3}}).encode()))
        return io.BytesIO(json.dumps({"ok": True, "result": {"message_id": 1}}).encode())

    tg = Telegram(token="x", opener=opener, sleep=slept.append)
    assert tg.send(1, "hi") == {"message_id": 1} and slept == [3]


def test_banner_confirmation_and_window():
    now = pd.Timestamp("2026-10-11 18:00", tz="UTC")
    post = lambda ch, h, t: dict(channel=ch, published_at=str(now - pd.Timedelta(hours=h)), text=t)  # noqa: E731
    lachen_only = banner.detect([post("lachentyt", 1, "Оголошується енергетичне перемир'я")], now)
    assert "не підтверджено" in banner.render(lachen_only)
    both = banner.detect([post("lachentyt", 1, "перемир'я"), post("V_Zelenskiy_official", 2, "Україна підтримує перемир'я")], now)
    assert "підтверджено: Президент" in banner.render(both)
    assert banner.detect([post("lachentyt", 30, "перемир'я")], now) == {}


def test_texts_fit_telegram():
    t = ui.evening_text("Київ", frame().iloc[0].to_dict(), 10, pd.Timestamp("2026-10-13"), "⚠️ банер")
    assert len(t) < 4096 and ui.DISCLAIMER in t
