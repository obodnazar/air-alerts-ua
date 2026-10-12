"""Turns one Telegram update into actions. No network here, so every path is testable."""

from __future__ import annotations

from . import catalog, ui


class Router:
    def __init__(self, store, cat: dict, tg, now_forecast):
        """now_forecast(unit, cp) -> text for /now (reads the cache)."""
        self.store, self.cat, self.tg, self.now_forecast = store, cat, tg, now_forecast
        self.oblasts = list(cat["oblasts"])
        self.units = cat["units"]
        self.index = {u: i for i, u in enumerate(self.units)}

    def handle(self, update: dict) -> None:
        if "callback_query" in update:
            return self._callback(update["callback_query"])
        msg = update.get("message") or {}
        chat, text = msg.get("chat", {}).get("id"), (msg.get("text") or "").strip()
        if not chat or not text:
            return
        cmd = text.split()[0].split("@")[0].lower()
        if cmd == "/start":
            self.tg.send(chat, ui.WELCOME, ui.oblast_keyboard(self.oblasts))
        elif cmd == "/help":
            self.tg.send(chat, ui.HELP)
        elif cmd == "/stop":
            if self.store.get(chat):
                self.store.set(chat, "active", 0)
            self.tg.send(chat, "Відписано. Щоб повернутися, натисніть /start.")
        elif cmd == "/settings":
            sub = self.store.get(chat)
            if not sub or not sub["unit"]:
                self.tg.send(chat, "Спершу оберіть район: /start")
            else:
                self.tg.send(chat, f"Налаштування для: {sub['unit']}", ui.settings_keyboard(sub))
        elif cmd == "/now":
            sub = self.store.get(chat)
            if not sub or not sub["unit"]:
                self.tg.send(chat, "Спершу оберіть район: /start")
            else:
                self.tg.send(chat, self.now_forecast(sub["unit"], sub["cp"]))
        elif not text.startswith("/"):
            hits = catalog.search(text, self.cat)
            served = [h for h in hits if h[1]]
            if hits and hits[0][1] is None:  # best match is not served: say so, do not offer a look-alike town
                self.tg.send(chat, f"{hits[0][0].split(' →')[0]}: район зараз не обслуговується "
                                   "(окупація або постійна тривога). Оберіть інший:", ui.oblast_keyboard(self.oblasts))
            elif served:
                self.tg.send(chat, "Знайшов. Оберіть:", ui.search_keyboard(hits, self.index))
            elif hits:
                self.tg.send(chat, "Цей район зараз не обслуговується (окупація або постійна тривога). Оберіть інший:",
                             ui.oblast_keyboard(self.oblasts))
            else:
                self.tg.send(chat, "Не знайшов такого міста чи району. Оберіть зі списку:", ui.oblast_keyboard(self.oblasts))
        else:
            self.tg.send(chat, ui.HELP)

    def _callback(self, q: dict) -> None:
        chat, mid, data = q["message"]["chat"]["id"], q["message"]["message_id"], q.get("data", "")
        kind, _, val = data.partition(":")
        if kind == "ob":
            oblast = self.oblasts[int(val)]
            self.tg.edit(chat, mid, f"{oblast}: оберіть район", ui.raion_keyboard(self.cat["oblasts"][oblast], self.index))
        elif kind == "back":
            self.tg.edit(chat, mid, "Оберіть область:", ui.oblast_keyboard(self.oblasts))
        elif kind == "u":
            unit = self.units[int(val)]
            self.store.subscribe(chat, unit)
            sub = self.store.get(chat)
            self.tg.edit(chat, mid, f"✅ Підписано: {unit}.\nПрогноз прийде о 20:00. Контрольна точка школи: "
                                    f"{sub['cp']:02d}:00 (змінити: /settings). Прогноз зараз: /now")
        elif kind in ("cp", "m"):
            if kind == "cp":
                self.store.set(chat, "cp", int(val))
            else:
                self.store.set(chat, "morning", val)
            sub = self.store.get(chat)
            self.tg.edit(chat, mid, f"Налаштування для: {sub['unit']}", ui.settings_keyboard(sub))
        self.tg.answer(q["id"])
