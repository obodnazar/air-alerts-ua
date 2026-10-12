"""The bot without Telegram: a fake transport records what would be sent."""

from __future__ import annotations

import io
import json
import urllib.error

import pandas as pd
import pytest

from airalerts.botapp import banner, catalog, ui
from airalerts.botapp.main import broadcast, due, due_slots
from airalerts.botapp.router import Router
from airalerts.botapp.store import Store
from airalerts.botapp.telegram import Blocked, Telegram

pytestmark = pytest.mark.skipif(not catalog.CATALOG.exists(), reason="catalog snapshot missing")
KYIV = "Europe/Kyiv"


class FakeTG:
    def __init__(self, blocked=(), broken=()):
        self.calls, self.blocked, self.broken = [], set(blocked), set(broken)

    def send(self, chat, text, keyboard=None):
        if chat in self.blocked:
            raise Blocked("blocked")
        if chat in self.broken:
            raise RuntimeError("network")
        self.calls.append(("send", chat, text, keyboard))

    def edit(self, chat, mid, text, keyboard=None):
        self.calls.append(("edit", chat, text, keyboard))

    def answer(self, cid, text=None):
        self.calls.append(("answer", cid, text))


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def bot():
    cat, tg, st, clock = catalog.load(), FakeTG(), Store(":memory:"), Clock()
    return Router(st, cat, tg, lambda sub: f"now {sub['units']}", clock=clock), tg, st, cat, clock


def msg(text, chat=1):
    return {"message": {"chat": {"id": chat}, "text": text}}


def cb(data, chat=1):
    return {"callback_query": {"id": "q", "data": data, "message": {"chat": {"id": chat}, "message_id": 5}}}


def buttons(keyboard):
    return [b for row in keyboard["inline_keyboard"] for b in row]


def unit_cb(cat, name):
    return f"u:{cat['units'].index(name)}"


# ---------- navigation and search ----------

def test_start_shows_oblasts_and_buttons_fit_telegram_limit(bot):
    r, tg, _, cat, _ = bot
    r.handle(msg("/start"))
    kb = tg.calls[-1][3]
    assert len(buttons(kb)) == len(cat["oblasts"])
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons(kb))


def test_every_raion_reachable_through_buttons(bot):
    r, tg, _, cat, clock = bot
    reached = set()
    for i, _ in enumerate(cat["oblasts"]):
        clock.t += 10  # stay under the rate limit
        r.handle(cb(f"ob:{i}"))
        reached |= {b["callback_data"] for b in buttons(tg.calls[-2][3]) if b["callback_data"].startswith("u:")}
    assert len(reached) == len(cat["units"]) == 104


@pytest.mark.parametrize("query,expected", [("Біла Церква", "Білоцерківський район"), ("Ірпінь", "Бучанський район"),
                                            ("Кривий Ріг", "Криворізький район"), ("Київ", "Київ"), ("Одеса", "Одеський район")])
def test_town_search(bot, query, expected):
    r, tg, _, cat, _ = bot
    r.handle(msg(query))
    first = buttons(tg.calls[-1][3])[0]["callback_data"]
    assert cat["units"][int(first.split(":")[1])] == expected


def test_occupied_town_is_not_offered(bot):
    r, tg, _, _, _ = bot
    r.handle(msg("Мелітополь"))
    assert "не обслуговується" in tg.calls[-1][2]


# ---------- customisation ----------

def test_up_to_three_raions_and_removal(bot):
    r, tg, st, cat, clock = bot
    for name in ("Київ", "Бучанський район", "Броварський район", "Обухівський район"):
        clock.t += 10
        r.handle(cb(unit_cb(cat, name)))
    assert st.get(1)["units"] == ["Київ", "Бучанський район", "Броварський район"]
    assert "щонайбільше" in tg.calls[-1][2]  # the 4th one is refused with a note
    r.handle(cb(f"rm:{cat['units'].index('Київ')}"))
    assert st.get(1)["units"] == ["Бучанський район", "Броварський район"]


def test_settings_change_and_pause(bot):
    r, _, st, cat, clock = bot
    r.handle(cb(unit_cb(cat, "Київ")))
    for data in ("cp:11", "ev:21:00", "st:careful", "m:always", "bn:0", "p:7"):
        clock.t += 2
        r.handle(cb(data))
    s = st.get(1)
    assert (s["cp"], s["evening"], s["style"], s["morning"], s["banner"]) == (11, "21:00", "careful", "always", 0)
    assert s["paused_until"] is not None
    tomorrow = (pd.Timestamp.now(tz=KYIV) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    assert st.active(tomorrow) == []  # paused: nothing is sent


# ---------- security ----------

@pytest.mark.parametrize("data", ["cp:99", "cp:abc", "cp:²", "bn:²", "u:99999", "u:-1", "u:²", "ob:abc", "ob:999", "ob:-1",
                                  "ev:03:00", "st:evil", "m:'; DROP TABLE subs;--", "bn:7", "p:365", "rm:abc", "xx:1", ""])
def test_forged_button_data_is_harmless(bot, data):
    r, _, st, cat, _ = bot
    r.handle(cb(unit_cb(cat, "Київ")))
    before = st.get(1)
    r.handle(cb(data))  # must not raise
    after = st.get(1)
    assert after == before
    assert st.db.execute("SELECT count(*) FROM subs").fetchone()[0] == 1
    assert r.tg.calls[-1][0] == "answer"  # the button never stays spinning


def test_remove_raion_for_unknown_chat_is_harmless(bot):
    r, tg, st, cat, _ = bot
    r.handle(cb("rm:0", chat=777))
    assert st.get(777) is None and tg.calls[-1][0] == "answer"


def test_global_rate_limit(bot):
    r, tg, _, _, _ = bot
    for chat in range(100):
        r.handle(msg("/start", chat=chat))
    assert sum(1 for c in tg.calls if c[0] == "send") == 25


def test_resubscribe_after_stop(bot):
    r, _, st, cat, clock = bot
    r.handle(cb(unit_cb(cat, "Київ")))
    r.handle(msg("/stop"))
    clock.t += 1
    r.handle(cb(unit_cb(cat, "Київ")))
    assert st.get(1)["active"] == 1


def test_two_connections_do_not_lock(tmp_path):
    a, b = Store(tmp_path / "x.sqlite"), Store(tmp_path / "x.sqlite")
    a.mark_sent(1, "evening", "2026-10-13")
    a.mark_sent(1, "evening", "2026-10-13")
    a.add_unit(1, "Київ")
    a.add_unit(1, "Київ")
    b.kv("x", "1")  # used to raise "database is locked"
    assert b.kv("x") == "1"


@pytest.mark.parametrize("query,expected", [("Буча", "Бучанський район"), ("Чернігів", "Чернігівський район"),
                                            ("Кам'янське", "Кам'янський район"), ("Кременчук", "Кременчуцький район"),
                                            ("Новомосковськ", "Самарівський район"), ("Дніпро", "Дніпровський район"),
                                            ("Запоріжжя", "Запорізький район"), ("Чернівці", "Чернівецький район")])
def test_city_ranking(query, expected):
    assert catalog.search(query, catalog.load())[0][1] == expected


def test_rate_limit_drops_spam(bot):
    r, tg, _, _, clock = bot
    for _ in range(50):
        r.handle(msg("/start"))
    assert sum(1 for c in tg.calls if c[0] == "send") == 8
    clock.t += 11
    r.handle(msg("/start"))
    assert sum(1 for c in tg.calls if c[0] == "send") == 9


def test_store_refuses_unknown_fields():
    st = Store(":memory:")
    st.add_unit(1, "Київ")
    assert not st.set(1, "chat_id", 5) and not st.set(1, "cp", 99)


# ---------- broadcasting ----------

def frame():
    return pd.DataFrame([dict(unit=u, p_alert_08=0.4, usual=0.3, **{f"p_clear_{c}": 0.2 + 0.1 * (c - 9) for c in (9, 10, 11, 12)})
                         for u in ("Київ", "Бучанський район", "Харківський район")])


def test_evening_slots_idempotency_blocked_and_broken():
    st, tg = Store(":memory:"), FakeTG(blocked={3}, broken={4})
    for chat, unit in ((1, "Київ"), (2, "Бучанський район"), (3, "Харківський район"), (4, "Київ"), (5, "Київ")):
        st.add_unit(chat, unit)
    st.add_unit(1, "Бучанський район")
    st.set(2, "cp", 12)
    st.set(5, "evening", "21:00")
    day = pd.Timestamp("2026-10-13")
    assert broadcast("evening", day, st, tg, frame(), "", slot="20:00", pause=0) == 2  # 1 and 2; 3 blocked, 4 broken
    texts = {c[1]: c[2] for c in tg.calls}
    assert "Київ" in texts[1] and "Бучанський" in texts[1]  # two raions in one message
    assert "до 12:00: 50%" in texts[2]                      # the subscriber's own control point
    assert st.get(3)["active"] == 0                         # blocked user switched off
    assert 5 not in texts                                   # 21:00 subscriber waits for their slot
    assert not st.kv("done:evening:20:00:2026-10-13")       # a transient failure keeps the slot open
    tg.broken.clear()                                       # network is back
    assert broadcast("evening", day, st, tg, frame(), "", slot="20:00", pause=0) == 1  # only the failed one, once
    assert st.kv("done:evening:20:00:2026-10-13") == "1"
    assert broadcast("evening", day, st, tg, frame(), "", slot="20:00", pause=0) == 0  # nothing twice
    assert broadcast("evening", day, st, tg, frame(), "", slot="21:00", pause=0) == 1


def test_morning_only_alert_unless_always_and_style():
    st, tg = Store(":memory:"), FakeTG()
    st.add_unit(1, "Київ")
    st.add_unit(2, "Бучанський район")
    st.set(2, "morning", "always")
    st.add_unit(3, "Київ")
    st.set(3, "style", "bold")
    m = pd.DataFrame([dict(unit="Київ", level="red", elapsed_min=130, p_clear_9=0.1, p_clear_10=0.3, p_clear_11=0.4, p_clear_12=0.5)])
    broadcast("morning", pd.Timestamp("2026-10-13"), st, tg, m, "⚠️ банер", pause=0)
    texts = {c[1]: c[2] for c in tg.calls}
    assert "оголосіть дистанційний день" in texts[1] and "⚠️ банер" in texts[1]
    assert "Тривоги немає" in texts[2]
    assert "чекайте" in texts[3]  # bold style calls a remote day only below 25%


@pytest.mark.parametrize("now,kind,expected", [
    ("2026-10-11 19:50", "evening", None),          # before the issue time: the state at 20:00 is not known yet
    ("2026-10-11 20:01", "evening", "2026-10-12"),  # Sunday evening -> Monday
    ("2026-10-16 20:01", "evening", None),          # Friday evening -> Saturday: no school
    ("2026-10-12 08:03", "morning", "2026-10-12"),
    ("2026-10-12 12:00", "morning", None),          # outside the window
])
def test_prepare_schedule(now, kind, expected):
    d = due(kind, pd.Timestamp(now, tz=KYIV), Store(":memory:"))
    assert (f"{d:%Y-%m-%d}" if d is not None else None) == expected


def test_slots_need_ready_forecast_and_respect_time():
    st = Store(":memory:")
    now = pd.Timestamp("2026-10-12 20:35", tz=KYIV)
    assert due_slots("evening", now, st) == []  # forecast not ready yet
    st.kv("ready:evening:2026-10-13", "1")
    assert [s for _, s in due_slots("evening", now, st)] == ["20:00", "20:30"]
    st.kv("done:evening:20:00:2026-10-13", "1")
    assert [s for _, s in due_slots("evening", now, st)] == ["20:30"]


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
    assert "не підтверджено" in banner.render(banner.detect([post("lachentyt", 1, "Оголошується перемир'я")], now))
    both = banner.detect([post("lachentyt", 1, "перемир'я"), post("V_Zelenskiy_official", 2, "підтримує перемир'я")], now)
    assert "підтверджено: Президент" in banner.render(both)
    assert banner.detect([post("lachentyt", 30, "перемир'я")], now) == {}


def test_texts_fit_telegram():
    sub = dict(units=["Київ", "Бучанський район", "Харківський район"], cp=10, style="balanced", banner=1)
    t = ui.evening_message(sub, {r["unit"]: r for r in frame().to_dict("records")}, pd.Timestamp("2026-10-13"), "⚠️ " * 200)
    assert len(t) < 4096 and ui.DISCLAIMER in t
