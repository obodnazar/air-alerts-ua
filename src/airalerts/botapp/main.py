"""Run the bot: long polling + a daily schedule (Kyiv time). School days only (Mon–Fri mornings).

    export TELEGRAM_BOT_TOKEN=...        # from @BotFather, never committed
    PYTHONPATH=src python -m airalerts.botapp.main

Schedule (each job runs once per day; if the bot starts late it catches up inside the window):
  19:45–21:30  prepare tomorrow's evening forecast (refresh data, compute all raions) → send to subscribers
  08:01–09:30  prepare the 08:00 update → send (alert in your raion, or "no alert" if you chose "always")
"""

from __future__ import annotations

import threading
import time

import pandas as pd

from . import banner, catalog, forecasts, ui
from .router import Router
from .store import Store
from .telegram import Blocked, Telegram

KYIV = "Europe/Kyiv"
COMMANDS = [{"command": "start", "description": "обрати район"}, {"command": "now", "description": "прогноз на завтра"},
            {"command": "settings", "description": "контрольна точка, ранкові повідомлення"},
            {"command": "stop", "description": "відписатися"}, {"command": "help", "description": "як це працює"}]
PREPARE = {"evening": ("19:45", "21:30"), "morning": ("08:01", "09:30")}
SLOTS = {"evening": ("20:00", "20:30", "21:00"), "morning": ("08:05",)}
LATE = 90  # minutes a slot may still be delivered after its time (bot started late, network hiccup)


def target_day(kind: str, now: pd.Timestamp) -> pd.Timestamp | None:
    """The school day a job is for, or None if it is not a school day."""
    d = now.normalize().tz_localize(None) + pd.Timedelta(days=1 if kind == "evening" else 0)
    return d if d.weekday() < 5 else None


def due(kind: str, now: pd.Timestamp, store: Store) -> pd.Timestamp | None:
    """Is the forecast for `kind` due to be prepared now? Returns the school day or None."""
    a, b = PREPARE[kind]
    if not (a <= now.strftime("%H:%M") <= b):
        return None
    d = target_day(kind, now)
    if d is None or store.kv(f"ready:{kind}:{d:%Y-%m-%d}"):
        return None
    failed = store.kv(f"fail:{kind}:{d:%Y-%m-%d}")
    if failed and time.time() - float(failed) < 600:  # a failed job retries at most every 10 min
        return None
    return d


def due_slots(kind: str, now: pd.Timestamp, store: Store) -> list[tuple[pd.Timestamp, str]]:
    """Delivery slots whose time has come, whose forecast is ready and which were not sent yet."""
    d = target_day(kind, now)
    if d is None or not store.kv(f"ready:{kind}:{d:%Y-%m-%d}"):
        return []
    hhmm, out = now.strftime("%H:%M"), []
    for slot in SLOTS[kind]:
        late = (pd.Timestamp(f"2000-01-01 {slot}") + pd.Timedelta(minutes=LATE)).strftime("%H:%M")
        if slot <= hhmm <= late and not store.kv(f"done:{kind}:{slot}:{d:%Y-%m-%d}"):
            out.append((d, slot))
    return out


def broadcast(kind: str, day: pd.Timestamp, store: Store, tg, frame: pd.DataFrame, banner_text: str,
              slot: str | None = None, pause: float = 0.05) -> int:
    rows = {r["unit"]: r for r in frame.to_dict("records")}
    sent = 0
    for sub in store.active(f"{day:%Y-%m-%d}"):
        if kind == "evening" and slot and sub["evening"] != slot:
            continue
        if not store.mark_sent(sub["chat_id"], kind, f"{day:%Y-%m-%d}"):
            continue  # already sent today (restart-safe)
        try:
            text = (ui.evening_message if kind == "evening" else ui.morning_message)(sub, rows, day, banner_text)
            if text:
                tg.send(sub["chat_id"], text)
                sent += 1
        except Blocked:
            store.set(sub["chat_id"], "active", 0)
        except Exception as e:  # one bad subscriber must never stop the others
            print(f"send to one subscriber failed: {type(e).__name__}", flush=True)
        time.sleep(pause)  # stay far below ~30 messages/s
    if slot:
        store.kv(f"done:{kind}:{slot}:{day:%Y-%m-%d}", "1")
    return sent


def prepare(kind: str, day: pd.Timestamp, store_path, refresh=True):
    from ..live import refresh as refresh_data

    store = Store(store_path)  # own connection: runs in a worker thread
    try:
        if refresh:
            refresh_data()
        (forecasts.evening if kind == "evening" else forecasts.morning)(f"{day:%Y-%m-%d}")
        store.kv(f"banner:{kind}:{day:%Y-%m-%d}", banner.fetch(pd.Timestamp.now(tz="UTC")) or "-")
        store.kv(f"ready:{kind}:{day:%Y-%m-%d}", "1")
        print(f"{kind} forecast for {day:%Y-%m-%d} ready", flush=True)
    except Exception as e:
        store.kv(f"fail:{kind}:{day:%Y-%m-%d}", str(time.time()))
        print(f"{kind} prepare failed: {type(e).__name__}: {e}", flush=True)


def main() -> None:
    tg = Telegram()
    store_path = "data/bot/bot.sqlite"
    store = Store(store_path)
    cat = catalog.load()
    tg.call("setMyCommands", commands=COMMANDS)

    def now_forecast(sub: dict) -> str:
        now = pd.Timestamp.now(tz=KYIV)
        day = now.normalize().tz_localize(None) + pd.Timedelta(days=1)
        while day.weekday() >= 5:
            day += pd.Timedelta(days=1)
        frame = forecasts.cached("evening", f"{day:%Y-%m-%d}")
        if frame is None:
            return "Прогноз на наступний шкільний день з'явиться о 19:45. До того: /help"
        rows = {r["unit"]: r for r in frame.to_dict("records")}
        return ui.evening_message(sub, rows, day) or "Для ваших районів прогнозу немає."

    router = Router(store, cat, tg, now_forecast)
    running: dict[str, threading.Thread] = {}
    offset = None
    print("bot running; Ctrl-C to stop", flush=True)
    while True:
        try:
            updates = tg.call("getUpdates", timeout=25, offset=offset, allowed_updates=["message", "callback_query"])
        except Exception as e:  # network hiccup: keep the bot alive
            print("poll error:", e, flush=True)
            time.sleep(5)
            updates = []
        for u in updates or []:
            offset = u["update_id"] + 1
            try:
                router.handle(u)
            except Blocked:
                pass
            except Exception as e:
                print("handler error:", e, flush=True)
        now = pd.Timestamp.now(tz=KYIV)
        for kind in ("evening", "morning"):
            d = due(kind, now, store)
            key = f"{kind}:{d}"
            if d is not None and not (key in running and running[key].is_alive()):
                running[key] = threading.Thread(target=prepare, args=(kind, d, store_path), daemon=True)
                running[key].start()
            for day, slot in due_slots(kind, now, store):
                frame = forecasts.cached(kind, f"{day:%Y-%m-%d}")
                text = store.kv(f"banner:{kind}:{day:%Y-%m-%d}")
                n = broadcast(kind, day, store, tg, frame, "" if text in (None, "-") else text, slot=slot)
                print(f"{kind} {slot} {day:%Y-%m-%d}: sent {n}", flush=True)


if __name__ == "__main__":
    main()
