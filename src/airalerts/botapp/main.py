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
WINDOWS = {"evening": ("19:45", "21:30"), "morning": ("08:01", "09:30")}


def target_day(kind: str, now: pd.Timestamp) -> pd.Timestamp | None:
    """The school day a job is for, or None if it is not a school day."""
    d = now.normalize().tz_localize(None) + pd.Timedelta(days=1 if kind == "evening" else 0)
    return d if d.weekday() < 5 else None


def due(kind: str, now: pd.Timestamp, store: Store) -> pd.Timestamp | None:
    a, b = WINDOWS[kind]
    if not (a <= now.strftime("%H:%M") <= b):
        return None
    d = target_day(kind, now)
    if d is None or store.kv(f"done:{kind}:{d:%Y-%m-%d}"):
        return None
    failed = store.kv(f"fail:{kind}:{d:%Y-%m-%d}")
    if failed and time.time() - float(failed) < 600:  # a failed job retries at most every 10 min
        return None
    return d


def broadcast(kind: str, day: pd.Timestamp, store: Store, tg, frame: pd.DataFrame, banner_text: str, pause: float = 0.05):
    rows = {r["unit"]: r for r in frame.to_dict("records")}
    sent = 0
    for sub in store.active():
        if not store.mark_sent(sub["chat_id"], kind, f"{day:%Y-%m-%d}"):
            continue  # already sent today (restart-safe)
        if kind == "evening":
            if sub["unit"] not in rows:
                continue
            text = ui.evening_text(sub["unit"], rows[sub["unit"]], sub["cp"], day, banner_text)
        else:
            row = rows.get(sub["unit"])
            if row is None and sub["morning"] != "always":
                continue
            text = ui.morning_text(sub["unit"], row, sub["cp"], day, banner_text)
        try:
            tg.send(sub["chat_id"], text)
            sent += 1
        except Blocked:
            store.set(sub["chat_id"], "active", 0)
        time.sleep(pause)  # stay far below ~30 messages/s
    store.kv(f"done:{kind}:{day:%Y-%m-%d}", "1")
    return sent


def run_job(kind: str, day: pd.Timestamp, store_path, tg, refresh=True):
    from ..live import refresh as refresh_data

    store = Store(store_path)  # own connection: runs in a worker thread
    try:
        if refresh:
            refresh_data()
        frame = forecasts.evening(f"{day:%Y-%m-%d}") if kind == "evening" else forecasts.morning(f"{day:%Y-%m-%d}")
        text = banner.fetch(pd.Timestamp.now(tz="UTC"))
        n = broadcast(kind, day, store, tg, frame, text)
        print(f"{kind} {day:%Y-%m-%d}: sent {n}", flush=True)
    except Exception as e:
        store.kv(f"fail:{kind}:{day:%Y-%m-%d}", str(time.time()))
        print(f"{kind} job failed: {e}", flush=True)


def main() -> None:
    tg = Telegram()
    store_path = "data/bot/bot.sqlite"
    store = Store(store_path)
    cat = catalog.load()
    tg.call("setMyCommands", commands=COMMANDS)

    def now_forecast(unit: str, cp: int) -> str:
        now = pd.Timestamp.now(tz=KYIV)
        day = now.normalize().tz_localize(None) + pd.Timedelta(days=1)
        while day.weekday() >= 5:
            day += pd.Timedelta(days=1)
        frame = forecasts.cached("evening", f"{day:%Y-%m-%d}")
        if frame is None:
            return "Прогноз на наступний шкільний день з'явиться о 19:45. До того: /help"
        row = frame.set_index("unit").loc[unit].to_dict() if unit in set(frame.unit) else None
        return ui.evening_text(unit, row, cp, day) if row else "Для цього району прогнозу немає."

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
                running[key] = threading.Thread(target=run_job, args=(kind, d, store_path, tg), daemon=True)
                running[key].start()


if __name__ == "__main__":
    main()
