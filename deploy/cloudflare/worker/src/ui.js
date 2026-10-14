// Texts and keyboards, ported 1:1 from src/airalerts/botapp/ui.py (v3, mobile-first).
import { escapeHtml as esc } from "./util.js";

export const DOW = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"];
const LEVEL = { red: "🔴 червона тривога", yellow: "🟡 жовта тривога" };
export const DISCLAIMER = "<i>Прогноз, а не сигнал. Слухайте офіційні тривоги.</i>";
// label, evening threshold (advise remote if P(remote day) > x), morning threshold (call remote if P(clear) < y)
export const STYLE = { careful: ["🛡 Обережно", 0.25, 0.45], balanced: ["⚖️ Баланс", 0.35, 0.35], bold: ["⚡ Сміливо", 0.45, 0.25] };

// First contact: who we are and what to expect, then one clear action (audit of the UX, 12 Oct).
export const WELCOME = "🌅 <b>Шкільний ранок</b>\n" +
  "<i>Прогноз повітряних тривог для шкільного дня</i>\n\n" +
  "Щовечора я пишу для вашого району:\n" +
  "🚨 чи буде тривога о <b>08:00</b>;\n" +
  "🔔 чи встигне відбій до уроків.\n\n" +
  "<b>Як це виглядає</b>\n" +
  "🌙 <b>~20:05</b> · прогноз на завтра і порада: очно чи дистанційно\n" +
  "🌅 <b>08:05</b> · якщо тривога вже є: чекати відбою чи оголошувати дистанційку\n\n" +
  "<b>Хто ми</b>\n" +
  "Некомерційний студентський проєкт. Модель навчена на історії тривог з грудня 2025 і перевірена на " +
  "вересні–жовтні 2026: вона точніша за просте правило «як зазвичай». Код відкритий.\n\n" +
  "<blockquote>⚠️ Це прогноз для планування, а не сигнал безпеки. Під час тривоги йдіть в укриття " +
  "і слухайте офіційні сповіщення.</blockquote>";
export const welcomeKeyboard = () => kb([[["📍 Обрати мій район", "back", "primary"]], [["🔍 Як читати прогноз", "how"]]]);

export const welcomeBack = (s) => `👋 <b>З поверненням!</b>\nСтежу за: ${esc(s.units.map(short).join(", "))}.` +
  (s.active ? "" : "\n\nВи були відписані: оберіть район, щоб повернутися.");
export const welcomeBackKeyboard = (s) => kb([
  [["🌙 Прогноз зараз", "now", "primary"]],
  [["🕐 Ризик по годинах", "hr"]],
  [["📜 Останні тривоги", "rc"]],
  [["⚙️ Налаштування", "s:root"], ["＋ Район", "add"]],
  [["❓ Допомога", "help"]],
].filter((row, i) => s.active || i !== 0));

export const DB_DOWN = "⚠️ <b>Бот тимчасово не може прочитати свою базу</b> (ліміт безкоштовного сервера).\n" +
  "Спробуйте трохи пізніше. Під час тривоги: укриття й офіційні сповіщення.";

export const HOW = "🔍 <b>Як читати прогноз</b>\n\n" +
  "<b>Бучанський</b>\n🔔 Середній ризик · тривога о 08:00: <b>40%</b>\n▰▰▰▰▱▱▱▱▱▱ <i>зазвичай 30%</i>\n" +
  "Якщо буде, відбій до 10:00: 30% · увесь день дистанційно: 28%\n→ <b>Плануйте очний день</b>\n\n" +
  "• <b>🔕 / 🔔 / 🚨</b>: низький (до 20%), середній (20–50%) і високий (від 50%) ризик тривоги о 08:00.\n" +
  "• <b>Смужка</b>: та сама ймовірність, 1 поділка = 10%.\n" +
  "• <b>«зазвичай»</b>: як часто тривога о 08:00 була в цьому районі за останні 28 днів.\n" +
  "• <b>Контрольна точка</b>: якщо відбій прийде до неї, діти йдуть на очні уроки (правило МОН). " +
  "Вашу школу можна обрати в налаштуваннях: 09:00–12:00.\n" +
  "• <b>Порада</b> залежить від стилю: 🛡 обережно, ⚖️ баланс, ⚡ сміливо.";
export const howKeyboard = () => kb([[["📍 Обрати мій район", "back", "primary"]], back()]);

export const HELP = "❓ <b>Допомога</b>\n\n" +
  "<b>Команди</b>\n" +
  "🌙 /now · прогноз на найближчий шкільний день\n" +
  "🕐 /hours · ризик тривоги на обрану годину сьогодні чи завтра\n" +
  "📜 /recent · останні тривоги у ваших районах\n" +
  "⚙️ /settings · райони, контрольна точка, час прогнозу, стиль порад\n" +
  "📍 /start · обрати або додати район (до 3)\n" +
  "⏸ /pause · пауза на канікули чи хворобу\n" +
  "🔕 /stop · відписатися\n" +
  "❓ /help · ця довідка\n\n" +
  "<b>Підказка:</b> просто напишіть назву міста чи села, і я знайду ваш район.\n\n" +
  "<b>Коли приходять повідомлення</b>\n" +
  "🌙 щовечора з неділі по четвер, близько 20:05 (або 20:30 чи 21:00, як оберете)\n" +
  "🌅 о 08:05 у шкільні дні, лише якщо у вашому районі тривога (можна «щоранку»)\n\n" +
  "<blockquote expandable>📊 <b>Про модель</b>\n" +
  "Навчена на тривогах з грудня 2025 по районах, перевірена на вересні–жовтні 2026 без підглядання в майбутнє. " +
  "Прогноз о 07:00 точніший за найкраще просте правило на 7.8%, вечірній на 5.1%. " +
  "Правило МОН (лист №1/19575): тривога о 08:00 означає дистанційний старт, відбій до контрольної точки означає очні уроки.\n" +
  "Код і методика: github.com/obodnazar/air-alerts-ua</blockquote>\n\n" +
  DISCLAIMER;
export const helpKeyboard = () => kb([[["🔍 Як читати прогноз", "how"]], [["⚙️ Налаштування", "s:root"]], back()]);

/** "← Назад" on every screen reached by a button, so a mis-tap is one tap away from undone. */
export const back = (data = "home", label = "← Назад") => [[label, data]];
export const menuText = (s) => `🏠 <b>Меню</b>\nСтежу за: ${esc(s.units.map(short).join(", "))}.` +
  (s.active ? "" : "\n\nВи відписані: щоб повернутися, оберіть район.");

const btn = (text, data, style) => (style === "primary" || style === "success" ? { text, callback_data: data, style } : { text, callback_data: data });
export const kb = (rows) => ({ inline_keyboard: rows.map((r) => r.map((b) => btn(...b))) });
export const short = (u) => u.replace(" район", "");

function pairs(buttons, limit = 14) {
  const rows = [];
  for (let i = 0; i < buttons.length;) {
    if (i + 1 < buttons.length && buttons[i][0].length <= limit && buttons[i + 1][0].length <= limit) {
      rows.push([buttons[i], buttons[i + 1]]); i += 2;
    } else { rows.push([buttons[i]]); i += 1; }
  }
  return rows;
}

export const oblastKeyboard = (oblasts, to = "home") => kb([...pairs(oblasts.map((o, i) => [o.replace(" область", "").replace("м. ", ""), `ob:${i}`])), back(to)]);
export const raionKeyboard = (units, index) => kb([...units.map((u) => [[short(u), `u:${index[u]}`]]), [["← Області", "back"]]]);

export function searchKeyboard(hits, index) {
  const seen = new Set(), rows = [];
  for (const [, u] of hits) if (u && !seen.has(u)) { seen.add(u); rows.push([[`📍 ${short(u)}`, `u:${index[u]}`]]); }
  return kb([...rows, [["Обрати зі списку", "back"]], back()]);
}

export function searchText(hits) {
  const names = [...new Set(hits.filter(([, u]) => u).map(([l]) => esc(l.split(" →")[0].replace(" міська громада", "").replace(" громада", ""))))];
  return "Знайшов 👇" + (names.length ? `\n<i>${names.join(", ")}</i>` : "");
}

export const doneText = (s) => `✅ <b>Готово!</b> Стежу за: ${esc(s.units.map(short).join(", "))}.\n\n` +
  `🌙 Прогноз щовечора (нд–чт) близько <b>${s.evening === "20:00" ? "20:05" : s.evening}</b>\n` +
  `🔔 Контрольна точка школи: <b>${String(s.cp).padStart(2, "0")}:00</b>\n\n` +
  `<i>Змінити це можна в налаштуваннях.</i>`;
export const doneKeyboard = () => kb([[["🌙 Прогноз зараз", "now", "primary"]], [["🕐 Ризик по годинах", "hr"]],
  [["📜 Останні тривоги", "rc"]], [["⚙️ Налаштувати", "s:root"]]]);

export const settingsText = (s) => `⚙️ <b>Налаштування</b>${s.paused_until ? `\n⏸ Пауза до ${s.paused_until}` : ""}`;
export function settingsKeyboard(s) {
  return kb([
    [[`🏫 Райони · ${s.units.length}`, "s:units"]],
    [[`🔔 Контрольна точка · ${String(s.cp).padStart(2, "0")}:00`, "s:cp"]],
    [[`🌙 Прогноз о · ${s.evening}`, "s:ev"]],
    [[`🎚 Поради · ${STYLE[s.style][0].split(" ")[1]}`, "s:st"]],
    [[`🌅 Ранок · ${s.morning === "alert" ? "при тривозі" : "щоранку"}`, "s:m"]],
    [[`📰 Новини · ${s.banner ? "так" : "ні"}`, "s:bn"]],
    [["⏸ Пауза", "s:pause"]],
    back(),
  ]);
}

const tick = (on, label) => (on ? "✓ " : "") + label;
export const SCREENS = {
  cp: ["🔔 <b>Контрольна точка школи</b>\nДо цієї години має прийти відбій, щоб діти пішли на очні уроки (правило МОН).",
    (s) => [[9, 10, 11, 12].map((c) => [tick(s.cp === c, `${String(c).padStart(2, "0")}:00`), `cp:${c}`])]],
  ev: ["🌙 <b>Коли надсилати прогноз</b>", (s) => [["20:00", "20:30", "21:00"].map((t) => [tick(s.evening === t, t), `ev:${t}`])]],
  st: ["🎚 <b>Як охоче радити дистанційний ранок</b>\nБаланс: пороги, перевірені на даних.",
    (s) => Object.entries(STYLE).map(([k, v]) => [[tick(s.style === k, v[0]), `st:${k}`]])],
  m: ["🌅 <b>Ранкове повідомлення о 08:05</b>",
    (s) => [[[tick(s.morning === "alert", "🔔 При тривозі"), "m:alert"], [tick(s.morning === "always", "📅 Щоранку"), "m:always"]]]],
  bn: ["📰 <b>Банер новин</b>\nПро перемир'я чи попередження про масовану атаку. Цифри прогнозу він не змінює.",
    (s) => [[[tick(!!s.banner, "Так"), "bn:1"], [tick(!s.banner, "Ні"), "bn:0"]]]],
  pause: ["⏸ <b>Пауза</b>\nНа канікули чи хворобу, щоб я не писав.",
    () => [[["1 тиждень", "p:7"], ["2 тижні", "p:14"]], [["▶️ Відновити", "p:0"]]]],
};
export function screen(name, s) {
  const [text, rows] = SCREENS[name];
  return [text, kb([...rows(s), [["← Назад", "s:root"]]])];
}
export function unitsKeyboard(units, index, canAdd) {
  const rows = units.map((u) => [[`✕ ${short(u)}`, `rm:${index[u]}`]]);
  if (canAdd) rows.push([["＋ Додати район", "add", "primary"]]);
  return kb([...rows, [["← Назад", "s:root"]]]);
}

// ---------- forecasts ----------
export function mood(p) {
  if (p < 0.2) return "🔕 Низький ризик";
  if (p < 0.5) return "🔔 Середній ризик";
  return "🚨 Високий ризик";
}
/** Python's round(): halves go to the even neighbour (round(2.5) == 2), so JS and Python texts match. */
export function pyRound(x) {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 ? 2 * Math.round(x / 2) : r;
}
export function bar(p, n = 10) {
  const k = Math.max(0, Math.min(n, pyRound(p * n)));
  return "▰".repeat(k) + "▱".repeat(n - k);
}
const pct = (p) => `${pyRound(p * 100)}%`;
const cpLabel = (cp) => `${String(cp).padStart(2, "0")}:00`;

function eveningBlock(unit, r, cp, style) {
  const pa = r.p_alert_08, pc = r[`p_clear_${cp}`], pr = pa * (1 - pc);
  const advice = pr > STYLE[style][1] ? "Розгляньте дистанційний ранок"
    : pa >= 0.5 ? "Плануйте очний день, але можливий пізніший старт" : "Плануйте очний день";
  return `<b>${esc(short(unit))}</b>\n${mood(pa)} · тривога о 08:00: <b>${pct(pa)}</b>\n` +
    `${bar(pa)} <i>зазвичай ${pct(r.usual)}</i>\n` +
    `Якщо буде, відбій до ${cpLabel(cp)}: ${pct(pc)} · увесь день дистанційно: ${pct(pr)}\n→ <b>${advice}</b>`;
}
// live: liveState() for this unit at sending time, or null when the Worker's live data are stale (audit round 3:
// the 08:00 table cannot see an alert that started at 08:04, so "no alert" needs the live check or a time stamp)
function morningBlock(unit, r, cp, style, live) {
  if (!r && live?.on) return `<b>${esc(short(unit))}</b>\n🚨 <b>Тривога зараз</b> (почалась після 08:00).\n` +
    "→ <b>Пройдіть в укриття і стежте за офіційними сигналами.</b> Рішення про уроки ухвалює школа.";
  if (!r && live) return `<b>${esc(short(unit))}</b>\n🔕 Тривоги немає: очний день за розкладом.`;
  if (!r) return `<b>${esc(short(unit))}</b>\n🔕 О 08:00 тривоги не було: очний день за розкладом.\n` +
    "<i>Якщо тривога почнеться, діють звичайні правила: укриття й офіційні сигнали.</i>";
  const p = r[`p_clear_${cp}`];
  const advice = p < STYLE[style][2] ? "Оголосіть дистанційний день зараз" : `Чекайте контрольної точки ${cpLabel(cp)}`;
  return `<b>${esc(short(unit))}</b>\n${LEVEL[r.level] || "Тривога"} триває ${pyRound(r.elapsed_min)} хв\n` +
    `${bar(p)} відбій до ${cpLabel(cp)}: <b>${pct(p)}</b>\n→ <b>${advice}</b>`;
}
const tail = (s, banner) => [...(banner && s.banner ? [esc(banner)] : []), DISCLAIMER];

export function eveningMessage(s, rows, day, banner = "") {
  const blocks = s.units.filter((u) => rows[u]).map((u) => eveningBlock(u, rows[u], s.cp, s.style));
  if (!blocks.length) return null;
  const d = new Date(`${day}T12:00:00Z`);
  const head = `🌙 <b>Прогноз на ${DOW[(d.getUTCDay() + 6) % 7]} ${day.slice(8, 10)}.${day.slice(5, 7)}</b>`;
  const how = "<blockquote expandable>Тривога о 08:00 означає дистанційний старт; відбій до контрольної точки означає очні " +
    "уроки (МОН, лист №1/19575). «Зазвичай» означає частку ранків з тривогою за останні 28 днів.</blockquote>";
  return [head, ...blocks, how, ...tail(s, banner)].join("\n\n");
}
export function morningMessage(s, rows, day, banner = "", live = {}) {
  const units = s.units.filter((u) => rows[u] || live[u]?.on || s.morning === "always");
  if (!units.length) return null;
  const blocks = units.map((u) => morningBlock(u, rows[u], s.cp, s.style, live[u]));
  return [`🌅 <b>${day.slice(8, 10)}.${day.slice(5, 7)} · 08:05</b>`, ...blocks, ...tail(s, banner)].join("\n\n");
}

// ---------- 🕐 risk by hour, 📜 recent alerts ----------
const LEVEL_ICON = { red: "🔴", yellow: "🟡" };
const LEVEL_WORD = { red: "червона", yellow: "жовта" };
export const PLANNING = "<i>Це ймовірність для планування, а не сигнал безпеки. Коли лунає тривога, йдіть в укриття.</i>";
const hh = (h) => `${String(h).padStart(2, "0")}:00`;
/** Kyiv clock for a UTC ms; offset from hourly.js (no Intl in hot paths). */
export function kyivClock(ms, offsetMin) {
  const d = new Date(ms + offsetMin * 60000);
  return { hm: `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`,
    dm: `${String(d.getUTCDate()).padStart(2, "0")}.${String(d.getUTCMonth() + 1).padStart(2, "0")}` };
}
export function duration(ms) {
  const m = Math.max(0, Math.round(ms / 60000));
  return m < 60 ? `${m} хв` : `${Math.floor(m / 60)} год${m % 60 ? ` ${m % 60} хв` : ""}`;
}
/** 1 тривога, 2 тривоги, 5 тривог, 11 тривог, 21 тривога */
export function alertsWord(n) {
  const a = n % 10, b = n % 100;
  return a === 1 && b !== 11 ? "тривога" : a >= 2 && a <= 4 && (b < 12 || b > 14) ? "тривоги" : "тривог";
}

export function hoursUnitKeyboard(units, index) {
  return kb([...units.map((u) => [[`📍 ${short(u)}`, `hr:${index[u]}`]]), back()]);
}
export function hoursDayKeyboard(i, many) {
  return kb([[["Сьогодні", `hr:${i}:0`], ["Завтра", `hr:${i}:1`]], many ? back("hr", "← Райони") : back()]);
}
/** Hour buttons, four to a row (phone width); today starts after the current hour. */
export function hoursKeyboard(i, d, from = 0) {
  const btns = [];
  for (let h = from; h < 24; h++) btns.push([hh(h), `hr:${i}:${d}:${h}`]);
  const rows = [];
  for (let k = 0; k < btns.length; k += 4) rows.push(btns.slice(k, k + 4));
  return kb([...rows, [["← День", `hr:${i}`]]]);
}
export const hoursAskText = (unit) => `🕐 <b>Ризик по годинах · ${esc(short(unit))}</b>\nОберіть день:`;
export const hoursPickText = (unit, d) => `🕐 <b>${esc(short(unit))} · ${d === 1 ? "завтра" : "сьогодні"}</b>\nОберіть годину:`;
export function hourResultKeyboard(i, d) {
  return kb([[["← Інша година", `hr:${i}:${d}`], [d === 1 ? "Сьогодні" : "Завтра", `hr:${i}:${1 - d}`]],
    [["📜 Останні тривоги", "rc"]], back("home", "← Меню")]);
}

/** The hour screen. risk: hourly.hourRisk(); live: live.liveState() or null; seen: ms of the last good scrape. */
export function hourText(unit, d, hour, risk, live, seen, offsetMin) {
  const head = `🕐 <b>${esc(short(unit))} · ${d === 1 ? "завтра" : "сьогодні"}, ${hh(hour)}</b>`;
  const at = seen ? kyivClock(seen, offsetMin).hm : null;
  const parts = [head];
  if (!live) {
    parts.push(`⚠️ Немає свіжих даних про тривоги зараз${at ? ` (останнє оновлення о ${at})` : ""}. ` +
      "Стежте за офіційними сповіщеннями.");
  } else if (live.on) {
    const lv = LEVEL_WORD[live.level];
    parts.push(`🚨 <b>Зараз у районі тривога</b>${lv ? ` (${lv})` : ""}, триває ${duration(live.elapsed * 60000)}` +
      `${live.stuck ? " (довше за добу: можливо, ми пропустили відбій)" : ""}.\n` +
      "<b>Будьте в укритті й слухайте офіційні сповіщення.</b> Не плануйте виходити до відбою.");
  } else parts.push(`Зараз: тривоги немає (дані на ${at}).`);
  const window28 = risk?.asOf ? ` (28 днів до ${risk.asOf.slice(8, 10)}.${risk.asOf.slice(5, 7)})` : " (28 днів)";
  const WHY = {
    gate: "Для такого горизонту наша модель не точніша за історію, тому показуємо історичну оцінку.",
    no_model: "Модель для цього випадку зараз не завантажена, тому показуємо історичну оцінку.",
    stale_live: "Свіжих даних про «зараз» немає, тому оцінка лише за історією.",
    short: "До цієї години менше години: модель перевірена від 1 години вперед, тому тут лише історія. Дивіться, чи є тривога зараз.",
    stale_params: "Параметри моделі застаріли, тому оцінка лише за історією.",
  };
  if (!risk) {
    parts.push("Для цього району ще немає даних по годинах.");
  } else if (risk.model != null) {
    const src = risk.version === 2 ? "оцінка моделі: хвилі тривог зараз і історія району" : "оцінка моделі за історією району";
    parts.push(`Ймовірність, що о ${hh(hour)} буде тривога: <b>${pct(risk.model)}</b>\n${bar(risk.model)} ` +
      `<i>${src}; зазвичай о ${hh(hour)}: ${pct(risk.clim)}</i>`);
  } else {
    // history IS the estimate where no model is shown: given as the probability, with its source and the reason
    parts.push(`Ймовірність, що о ${hh(hour)} буде тривога: <b>≈${pct(risk.clim)}</b>\n${bar(risk.clim)} ` +
      `<i>за історією району: о ${hh(hour)} тривога була у ${pct(risk.clim)} днів${window28}</i>\n` +
      `<i>${WHY[risk.reason] || WHY.gate}</i>`);
  }
  parts.push(PLANNING);
  return parts.join("\n\n");
}

/** blocks: {unit: {last: rows, todayCount, todayMs}} from Store.recent(). */
export function recentText(units, blocks, meta, now, offsetMin) {
  const parts = ["📜 <b>Останні тривоги</b>"];
  // "none today" is a claim; make it only when the history is complete and fresh (audit round 3)
  const complete = !!meta.ok && now - meta.ok <= 15 * 60000 && !(meta.gapTo > 0);
  for (const u of units) {
    const b = blocks[u] || { last: [], todayCount: 0, todayMs: 0 };
    const none = complete ? "Сьогодні тривог не було" : "Сьогодні: немає підтверджених даних";
    const lines = [`<b>${esc(short(u))}</b>`,
      b.todayCount ? `Сьогодні: ${b.todayCount} ${alertsWord(b.todayCount)}, разом ${duration(b.todayMs)}${complete ? "" : " (може бути неповно)"}` : none];
    for (const r of b.last) {
      const s = kyivClock(r.started, offsetMin);
      const icon = LEVEL_ICON[r.max_level] || "🚨";
      lines.push(r.ended == null
        ? `${icon} ${s.dm} ${s.hm} → <b>триває</b> · ${duration(now - r.started)}`
        : `${icon} ${s.dm} ${s.hm}–${kyivClock(r.ended, offsetMin).hm} · ${duration(r.ended - r.started)}`);
    }
    if (!b.last.length) lines.push(complete ? "За останні два тижні тривог у записах немає." : "Записів поки немає.");
    parts.push(lines.join("\n"));
  }
  const at = meta.ok ? kyivClock(meta.ok, offsetMin).hm : null;
  if (!meta.ok || now - meta.ok > 15 * 60000) parts.push(`⚠️ Дані давно не оновлювались${at ? ` (о ${at})` : ""}: список може бути неповним.`);
  else if (meta.gapTo > 0) parts.push("⚠️ Частину повідомлень каналу пропущено: список може бути неповним до найближчого оновлення.");
  parts.push(`<i>Джерело: @UkraineAlarmSignal${at ? `, оновлено о ${at}` : ""}. ${complete ? "Це факти, не прогноз. " : ""}` +
    "🔴 червона, 🟡 жовта (за найвищим рівнем).</i>");
  return parts.join("\n\n");
}
export const recentKeyboard = () => kb([[["🔄 Оновити", "rc:1"]], [["🕐 Ризик по годинах", "hr"]], back()]);
export const removeConfirm = (u, i) => [`Прибрати <b>${esc(short(u))}</b> зі списку?`,
  kb([[["✕ Так, прибрати", `rmy:${i}`]], back("s:units")])];
