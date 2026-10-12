// One Telegram update -> actions. Ported from src/airalerts/botapp/router.py; every callback value is validated.
import * as ui from "./ui.js";
import { search } from "./catalog.js";
import { MAX_UNITS } from "./store.js";
import { toInt, kyivNow, addDays, weekday } from "./util.js";

export class Router {
  constructor(store, cat, tg, now = () => new Date()) {
    Object.assign(this, { store, cat, tg, now });
    this.oblasts = Object.keys(cat.oblasts);
    this.units = cat.units;
    this.index = Object.fromEntries(this.units.map((u, i) => [u, i]));
  }
  unitAt(v) { const i = toInt(v); return i !== null && i >= 0 && i < this.units.length ? this.units[i] : null; }

  async nowForecast(sub) {
    let day = addDays(kyivNow(this.now()).day, 1);
    while (weekday(day) >= 5) day = addDays(day, 1);
    const f = await this.store.forecast("evening", day);
    if (!f) return "Прогноз на наступний шкільний день з'явиться після 20:00. До того: /help";
    const rows = Object.fromEntries(f.rows.map((r) => [r.unit, r]));
    return ui.eveningMessage(sub, rows, day) || "Для ваших районів прогнозу немає.";
  }

  async handle(update) {
    if (update.callback_query) {
      const q = update.callback_query, chat = q.message?.chat?.id;
      if (!chat || !(await this.store.allowed(chat, this.now().getTime()))) return;
      let note = null;
      try { note = await this.dispatch(q); } catch { note = "Щось пішло не так, спробуйте /start."; }
      await this.tg.answer(q.id, note);
      return;
    }
    const msg = update.message || {}, chat = msg.chat?.id, text = String(msg.text || "").trim().slice(0, 200);
    if (!chat || !text || !(await this.store.allowed(chat, this.now().getTime()))) return;
    const cmd = text.split(/\s/)[0].split("@")[0].toLowerCase();
    const sub = await this.store.get(chat);
    if (cmd === "/start") return this.tg.send(chat, ui.WELCOME, ui.oblastKeyboard(this.oblasts));
    if (cmd === "/help") return this.tg.send(chat, ui.HELP);
    if (cmd === "/stop") { if (sub) await this.store.set(chat, "active", 0); return this.tg.send(chat, "Відписано. Щоб повернутися, натисніть /start."); }
    if (["/settings", "/now", "/pause"].includes(cmd)) {
      if (!sub || !sub.units.length) return this.tg.send(chat, "Спершу оберіть район: /start");
      if (cmd === "/settings") return this.tg.send(chat, ui.settingsText(sub), ui.settingsKeyboard(sub));
      if (cmd === "/pause") return this.tg.send(chat, ...ui.screen("pause", sub));
      return this.tg.send(chat, await this.nowForecast(sub));
    }
    if (text.startsWith("/")) return this.tg.send(chat, ui.HELP);
    if (msg.message_id) await this.tg.react(chat, msg.message_id);
    const hits = search(text, this.cat);
    if (hits.length && !hits[0][1]) {
      return this.tg.send(chat, `${hits[0][0].split(" →")[0]}: район зараз не обслуговується (окупація або постійна тривога). Оберіть інший:`,
        ui.oblastKeyboard(this.oblasts));
    }
    if (hits.some((h) => h[1])) return this.tg.send(chat, ui.searchText(hits), ui.searchKeyboard(hits, this.index));
    return this.tg.send(chat, "Не знайшов такого міста чи району. Оберіть зі списку:", ui.oblastKeyboard(this.oblasts));
  }

  async settings(chat, mid) { const s = await this.store.get(chat); return this.tg.edit(chat, mid, ui.settingsText(s), ui.settingsKeyboard(s)); }

  async dispatch(q) {
    const chat = q.message.chat.id, mid = q.message.message_id, data = String(q.data || "").slice(0, 64);
    const i = data.indexOf(":"), kind = i < 0 ? data : data.slice(0, i), val = i < 0 ? "" : data.slice(i + 1);
    const sub = await this.store.get(chat);
    if (kind === "ob") {
      const n = toInt(val), ob = n !== null && n >= 0 && n < this.oblasts.length ? this.oblasts[n] : null;
      if (ob) await this.tg.edit(chat, mid, `${ob}: оберіть район`, ui.raionKeyboard(this.cat.oblasts[ob], this.index));
    } else if (kind === "back" || kind === "add") {
      await this.tg.edit(chat, mid, "Оберіть область:", ui.oblastKeyboard(this.oblasts));
    } else if (kind === "u" && this.unitAt(val)) {
      if (await this.store.addUnit(chat, this.unitAt(val))) await this.tg.edit(chat, mid, ui.doneText(await this.store.get(chat)), ui.doneKeyboard());
      else return `Можна стежити щонайбільше за ${MAX_UNITS} районами. Приберіть один у /settings.`;
    } else if (kind === "rm" && this.unitAt(val) && sub) {
      await this.store.removeUnit(chat, this.unitAt(val));
      const s = await this.store.get(chat);
      await this.tg.edit(chat, mid, "🏫 <b>Мої райони</b> (до 3)", ui.unitsKeyboard(s.units, this.index, s.units.length < MAX_UNITS));
    } else if (kind === "now" && sub) {
      await this.tg.send(chat, await this.nowForecast(sub));
    } else if (kind === "s" && sub) {
      if (val === "units") await this.tg.edit(chat, mid, "🏫 <b>Мої райони</b> (до 3)", ui.unitsKeyboard(sub.units, this.index, sub.units.length < MAX_UNITS));
      else if (val in ui.SCREENS) await this.tg.edit(chat, mid, ...ui.screen(val, sub));
      else await this.settings(chat, mid);
    } else if (kind === "p" && ["0", "7", "14"].includes(val) && sub) {
      const until = val === "0" ? null : addDays(kyivNow(this.now()).day, Number(val));
      await this.store.pause(chat, until);
      await this.settings(chat, mid);
      return val === "0" ? "Пауза знята ▶️" : `Пауза до ${until} ⏸`;
    } else if (["cp", "ev", "st", "m", "bn"].includes(kind) && sub) {
      const field = { cp: "cp", ev: "evening", st: "style", m: "morning", bn: "banner" }[kind];
      const value = field === "cp" || field === "banner" ? toInt(val) : val;
      const ok = await this.store.set(chat, field, value);
      await this.settings(chat, mid);
      return ok ? "Збережено ✓" : "Невідоме значення.";
    }
    return null;
  }
}
