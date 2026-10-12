"""Telegram bot that sends the template brief for one raion. No LLM: every number comes from the models.

Setup (once): create a bot with @BotFather, then in your own terminal
    export TELEGRAM_BOT_TOKEN=...            # never commit it
Run:
    PYTHONPATH=src python -m airalerts.bot poll                       # answers /start, /raion, /now, /stop
    PYTHONPATH=src python -m airalerts.bot send --issue 20:00         # evening brief for tomorrow, to every subscriber
    PYTHONPATH=src python -m airalerts.bot send --issue 08:00         # morning update for today
    ... add --dry-run to print the messages instead of sending (no token needed)
Schedule `send` with cron or GitHub Actions at 20:00 and 08:05 Kyiv time.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

from .brief import DOW, make_brief

try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

SUBS = Path("data/bot/subscribers.json")
DISCLAIMER = "Не замінює офіційних сигналів тривоги."
LEVEL = {"red": "🔴 червона", "yellow": "🟡 жовта"}


# ---------- Telegram Bot API over plain HTTP ----------

def api(method: str, **params):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", data=data, timeout=70) as r:
        return json.load(r)["result"]


def send(chat_id: int, text: str, dry: bool) -> None:
    if dry:
        print(f"--- to {chat_id}\n{text}\n")
    else:
        api("sendMessage", chat_id=chat_id, text=text)


# ---------- subscribers ----------

def load_subs() -> dict:
    return json.loads(SUBS.read_text(encoding="utf-8")) if SUBS.exists() else {}


def save_subs(s: dict) -> None:
    SUBS.parent.mkdir(parents=True, exist_ok=True)
    SUBS.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


def match_unit(query: str, units: list[str]) -> list[str]:
    q = query.lower().replace("’", "'").replace("ʼ", "'").strip()
    exact = [u for u in units if u.lower() == q or u.lower() == q + " район"]
    return exact or [u for u in units if q and q[:5] in u.lower()]


# ---------- messages ----------

def evening_text(r, day: pd.Timestamp) -> str:
    return (f"🏫 {r.unit} · {DOW[day.weekday()]} {day:%d.%m}\n"
            f"Шанс тривоги о 08:00: {r.p_alert_08:.0%} (зазвичай {r.usual:.0%})\n"
            f"Якщо буде, відбій до 10:00: {r.p_clear_by_10:.0%}\n"
            f"Повністю дистанційний день: {r.p_remote_day:.0%}\n"
            f"→ {r.advice}.\n{DISCLAIMER}")


def morning_text(unit: str, row, day: pd.Timestamp) -> str:
    if row is None:
        return f"🏫 {unit} · {day:%d.%m} 08:00\nЗараз тривоги немає: очний день за розкладом.\n{DISCLAIMER}"
    return (f"🏫 {unit} · {day:%d.%m} 08:00\n{LEVEL.get(row.level, 'тривога')} триває {row.elapsed_min:.0f} хв.\n"
            f"Відбій до 10:00: {row.p_clear_by_10:.0%} → {row.advice}.\n{DISCLAIMER}")


def broadcast(issue: str, day: str | None, dry: bool) -> None:
    subs = load_subs()
    if not subs:
        print("no subscribers")
        return
    now = pd.Timestamp.now(tz="Europe/Kyiv")
    d = pd.Timestamp(day) if day else (now.normalize().tz_localize(None) + pd.Timedelta(days=1 if issue == "20:00" else 0))
    units = sorted(set(subs.values()))
    b = make_brief(d.strftime("%Y-%m-%d"), units=units, issue=issue)
    rows = {r.unit: r for r in b.itertuples()}
    for chat, unit in subs.items():
        text = evening_text(rows[unit], d) if issue != "08:00" else morning_text(unit, rows.get(unit), d)
        send(int(chat), text, dry)


def poll(units: list[str]) -> None:
    offset = None
    print("bot is polling; Ctrl-C to stop")
    while True:
        for u in api("getUpdates", timeout=60, **({"offset": offset} if offset else {})):
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            chat, text = msg.get("chat", {}).get("id"), (msg.get("text") or "").strip()
            if not chat:
                continue
            subs = load_subs()
            if text.startswith("/start"):
                send(chat, "Привіт! Я надсилаю о 20:00 прогноз шкільного ранку для вашого району, "
                           "а о 08:00 оновлення, якщо триває тривога.\nНапишіть: /raion Бучанський (або /raion Київ).", False)
            elif text.startswith("/raion"):
                found = match_unit(text[6:], units)
                if len(found) == 1:
                    subs[str(chat)] = found[0]
                    save_subs(subs)
                    send(chat, f"Готово: {found[0]}. Прогноз прийде о 20:00.", False)
                else:
                    send(chat, "Уточніть район: " + (", ".join(found[:8]) if found else "не знайдено"), False)
            elif text.startswith("/stop"):
                subs.pop(str(chat), None)
                save_subs(subs)
                send(chat, "Відписано.", False)
            elif text.startswith("/now"):
                send(chat, "Рахую, це займає до хвилини…", False)
                unit = subs.get(str(chat))
                if unit:
                    tomorrow = pd.Timestamp.now(tz="Europe/Kyiv").normalize().tz_localize(None) + pd.Timedelta(days=1)
                    b = make_brief(tomorrow.strftime("%Y-%m-%d"), units=[unit])
                    send(chat, evening_text(next(b.itertuples()), tomorrow), False)
                else:
                    send(chat, "Спершу оберіть район: /raion …", False)
        time.sleep(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["poll", "send"])
    ap.add_argument("--issue", default="20:00", choices=["20:00", "08:00"])
    ap.add_argument("--day")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "send":
        broadcast(a.issue, a.day, a.dry_run)
    else:
        from .data import build_store, eligible_units
        st = build_store(Path("data/raw/vadimkin/official_data_uk.csv"), Path("data/interim/telegram_episodes.parquet"))
        poll(eligible_units(st))


if __name__ == "__main__":
    main()
