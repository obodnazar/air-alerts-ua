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

RATE = (8, 10.0)          # at most 8 actions per chat per 10 s; extra ones are dropped silently
GLOBAL = (25, 1.0)        # and at most 25 actions per second across all chats (Telegram allows ~30/s)


class Router:
    def __init__(self, store, cat: dict, tg, now_forecast, clock=time.monotonic):
        """now_forecast(sub) -> text for /now (reads the cache)."""
        self.store, self.cat, self.tg, self.now_forecast, self.clock = store, cat, tg, now_forecast, clock
        self.oblasts = list(cat["oblasts"])
        self.units = cat["units"]
        self.index = {u: i for i, u in enumerate(self.units)}
        self.hits = defaultdict(deque)
        self.all_hits = deque()

    def _allowed(self, chat) -> bool:
        now = self.clock()
        while self.all_hits and now - self.all_hits[0] > GLOBAL[1]:
            self.all_hits.popleft()
        if len(self.all_hits) >= GLOBAL[0]:
            return False
        if len(self.hits) > 10_000:  # forget idle chats so the table cannot grow without bound
            for c in [c for c, q in self.hits.items() if not q or now - q[-1] > RATE[1]]:
                del self.hits[c]
        q = self.hits[chat]
        while q and now - q[0] > RATE[1]:
            q.popleft()
        if len(q) >= RATE[0]:
            return False
        q.append(now)
        self.all_hits.append(now)
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
        mid = msg.get("message_id")
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
        elif cmd in ("/settings", "/now", "/pause"):
            if not sub or not sub["units"]:
                self.tg.send(chat, "Спершу оберіть район: /start")
            elif cmd == "/settings":
                self.tg.send(chat, ui.settings_text(sub), ui.settings_keyboard(sub))
            elif cmd == "/pause":
                self.tg.send(chat, *ui.screen("pause", sub))
            else:
                self.tg.send(chat, self.now_forecast(sub))
        elif not text.startswith("/"):
            if mid and hasattr(self.tg, "react"):
                self.tg.react(chat, mid)  # "seen" without an extra message
            hits = catalog.search(text, self.cat)
            if hits and hits[0][1] is None:  # best match is not served: say so, do not offer a look-alike town
                self.tg.send(chat, f"{hits[0][0].split(' →')[0]}: район зараз не обслуговується "
                                   "(окупація або постійна тривога). Оберіть інший:", ui.oblast_keyboard(self.oblasts))
            elif any(h[1] for h in hits):
                self.tg.send(chat, ui.search_text(hits), ui.search_keyboard(hits, self.index))
            else:
                self.tg.send(chat, "Не знайшов такого міста чи району. Оберіть зі списку:", ui.oblast_keyboard(self.oblasts))
        else:
            self.tg.send(chat, ui.HELP)

    def _unit(self, val: str) -> str | None:
        i = _int(val)
        return self.units[i] if i is not None and 0 <= i < len(self.units) else None

    def _settings(self, chat, mid):
        sub = self.store.get(chat)
        self.tg.edit(chat, mid, ui.settings_text(sub), ui.settings_keyboard(sub))

    def _callback(self, q: dict) -> None:
        try:
            note = self._dispatch(q)
        except Exception:  # never leave the user's button spinning
            note = "Щось пішло не так, спробуйте /start."
        self.tg.answer(q["id"], note)

    def _dispatch(self, q: dict) -> str | None:
        chat, mid, data = q["message"]["chat"]["id"], q["message"]["message_id"], str(q.get("data", ""))[:64]
        kind, _, val = data.partition(":")
        note = None
        if kind == "ob":
            i = _int(val)
            oblast = self.oblasts[i] if i is not None and 0 <= i < len(self.oblasts) else None
            if oblast:
                self.tg.edit(chat, mid, f"{oblast}: оберіть район", ui.raion_keyboard(self.cat["oblasts"][oblast], self.index))
        elif kind in ("back", "add"):
            self.tg.edit(chat, mid, "Оберіть область:", ui.oblast_keyboard(self.oblasts))
        elif kind == "u" and (unit := self._unit(val)):
            if self.store.add_unit(chat, unit):
                self.tg.edit(chat, mid, ui.done_text(self.store.get(chat)), ui.done_keyboard())
            else:
                note = f"Можна стежити щонайбільше за {MAX_UNITS} районами. Приберіть один у /settings."
        elif kind == "rm" and (unit := self._unit(val)) and self.store.get(chat):
            self.store.remove_unit(chat, unit)
            sub = self.store.get(chat)
            self.tg.edit(chat, mid, "🏫 Мої райони", ui.units_keyboard(sub["units"], self.index, len(sub["units"]) < MAX_UNITS))
        elif kind == "now" and (sub := self.store.get(chat)):
            self.tg.send(chat, self.now_forecast(sub))
        elif kind == "s" and (sub := self.store.get(chat)):
            if val == "units":
                self.tg.edit(chat, mid, "🏫 <b>Мої райони</b> (до 3)",
                             ui.units_keyboard(sub["units"], self.index, len(sub["units"]) < MAX_UNITS))
            elif val in ui.SCREENS:
                self.tg.edit(chat, mid, *ui.screen(val, sub))
            else:
                self._settings(chat, mid)
        elif kind == "p" and val in ("0", "7", "14") and self.store.get(chat):
            until = None if val == "0" else (pd.Timestamp.now(tz="Europe/Kyiv") + pd.Timedelta(days=int(val))).strftime("%Y-%m-%d")
            self.store.pause(chat, until)
            self._settings(chat, mid)
            note = "Пауза знята ▶️" if val == "0" else f"Пауза до {until} ⏸"
        elif kind in ("cp", "ev", "st", "m", "bn", "set") and self.store.get(chat):
            field = {"cp": "cp", "ev": "evening", "st": "style", "m": "morning", "bn": "banner"}.get(kind)
            if field:
                value = _int(val) if field in ("cp", "banner") else val
                note = "Збережено ✓" if self.store.set(chat, field, value) else "Невідоме значення."
            self._settings(chat, mid)
        return note


def _int(val: str) -> int | None:
    """Strict ASCII integer: "²".isdigit() is True but int("²") fails (audit, 12 Oct)."""
    return int(val) if val.isascii() and val.lstrip("-").isdigit() else None
