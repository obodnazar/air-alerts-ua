"""Turns one Telegram update into actions. No network here, so every path is testable.

Button data comes from the client and can be forged: every value is parsed defensively and
checked against a whitelist (Store.set) before it touches the database.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

import pandas as pd

from . import catalog, ui
from .store import MAX_UNITS

RATE = (8, 10.0)  # at most 8 actions per chat per 10 s; extra ones are dropped silently


class Router:
    def __init__(self, store, cat: dict, tg, now_forecast, clock=time.monotonic):
        """now_forecast(sub) -> text for /now (reads the cache)."""
        self.store, self.cat, self.tg, self.now_forecast, self.clock = store, cat, tg, now_forecast, clock
        self.oblasts = list(cat["oblasts"])
        self.units = cat["units"]
        self.index = {u: i for i, u in enumerate(self.units)}
        self.hits = defaultdict(deque)

    def _allowed(self, chat) -> bool:
        q, now = self.hits[chat], self.clock()
        while q and now - q[0] > RATE[1]:
            q.popleft()
        if len(q) >= RATE[0]:
            return False
        q.append(now)
        return True

    def handle(self, update: dict) -> None:
        if "callback_query" in update:
            q = update["callback_query"]
            chat = q.get("message", {}).get("chat", {}).get("id")
            if chat and self._allowed(chat):
                self._callback(q)
            return
        msg = update.get("message") or {}
        chat, text = msg.get("chat", {}).get("id"), (msg.get("text") or "").strip()[:200]
        if not chat or not text or not self._allowed(chat):
            return
        cmd = text.split()[0].split("@")[0].lower()
        sub = self.store.get(chat)
        if cmd == "/start":
            self.tg.send(chat, ui.WELCOME, ui.oblast_keyboard(self.oblasts))
        elif cmd == "/help":
            self.tg.send(chat, ui.HELP)
        elif cmd == "/stop":
            if sub:
                self.store.set(chat, "active", 0)
            self.tg.send(chat, "Відписано. Щоб повернутися, натисніть /start.")
        elif cmd in ("/settings", "/now"):
            if not sub or not sub["units"]:
                self.tg.send(chat, "Спершу оберіть район: /start")
            elif cmd == "/settings":
                self.tg.send(chat, ui.settings_text(sub), ui.settings_keyboard(sub))
            else:
                self.tg.send(chat, self.now_forecast(sub))
        elif not text.startswith("/"):
            hits = catalog.search(text, self.cat)
            if hits and hits[0][1] is None:  # best match is not served: say so, do not offer a look-alike town
                self.tg.send(chat, f"{hits[0][0].split(' →')[0]}: район зараз не обслуговується "
                                   "(окупація або постійна тривога). Оберіть інший:", ui.oblast_keyboard(self.oblasts))
            elif any(h[1] for h in hits):
                self.tg.send(chat, "Знайшов. Оберіть:", ui.search_keyboard(hits, self.index))
            else:
                self.tg.send(chat, "Не знайшов такого міста чи району. Оберіть зі списку:", ui.oblast_keyboard(self.oblasts))
        else:
            self.tg.send(chat, ui.HELP)

    def _unit(self, val: str) -> str | None:
        try:
            i = int(val)
        except ValueError:
            return None
        return self.units[i] if 0 <= i < len(self.units) else None

    def _settings(self, chat, mid):
        sub = self.store.get(chat)
        self.tg.edit(chat, mid, ui.settings_text(sub), ui.settings_keyboard(sub))

    def _callback(self, q: dict) -> None:
        chat, mid, data = q["message"]["chat"]["id"], q["message"]["message_id"], str(q.get("data", ""))[:64]
        kind, _, val = data.partition(":")
        note = None
        if kind == "ob":
            try:
                oblast = self.oblasts[int(val)] if 0 <= int(val) < len(self.oblasts) else None
            except ValueError:
                oblast = None
            if oblast:
                self.tg.edit(chat, mid, f"{oblast}: оберіть район", ui.raion_keyboard(self.cat["oblasts"][oblast], self.index))
        elif kind in ("back", "add"):
            self.tg.edit(chat, mid, "Оберіть область:", ui.oblast_keyboard(self.oblasts))
        elif kind == "u" and (unit := self._unit(val)):
            if self.store.add_unit(chat, unit):
                sub = self.store.get(chat)
                self.tg.edit(chat, mid, f"✅ Стежу за: {', '.join(sub['units'])}.\nПрогноз прийде о {sub['evening']}. "
                                        f"Контрольна точка школи: {sub['cp']:02d}:00. Налаштування: /settings · прогноз зараз: /now")
            else:
                note = f"Можна стежити щонайбільше за {MAX_UNITS} районами. Приберіть один у /settings."
        elif kind == "rm" and (unit := self._unit(val)):
            self.store.remove_unit(chat, unit)
            sub = self.store.get(chat)
            self.tg.edit(chat, mid, "🏫 Мої райони", ui.units_keyboard(sub["units"], self.index, len(sub["units"]) < MAX_UNITS))
        elif kind == "units" and (sub := self.store.get(chat)):
            self.tg.edit(chat, mid, "🏫 Мої райони", ui.units_keyboard(sub["units"], self.index, len(sub["units"]) < MAX_UNITS))
        elif kind == "pause":
            self.tg.edit(chat, mid, "Пауза: на канікули чи хворобу, щоб бот не писав.", ui.pause_keyboard())
        elif kind == "p" and val in ("0", "7", "14") and self.store.get(chat):
            until = None if val == "0" else (pd.Timestamp.now(tz="Europe/Kyiv") + pd.Timedelta(days=int(val))).strftime("%Y-%m-%d")
            self.store.pause(chat, until)
            self._settings(chat, mid)
        elif kind in ("cp", "ev", "st", "m", "bn", "set") and self.store.get(chat):
            field = {"cp": "cp", "ev": "evening", "st": "style", "m": "morning", "bn": "banner"}.get(kind)
            if field:
                value = int(val) if field in ("cp", "banner") and val.isdigit() else val
                if not self.store.set(chat, field, value):
                    note = "Невідоме значення."
            self._settings(chat, mid)
        self.tg.answer(q["id"], note)
