// Texts and keyboards, ported 1:1 from src/airalerts/botapp/ui.py (v3, mobile-first).
import { escapeHtml as esc } from "./util.js";

export const DOW = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"];
const LEVEL = { red: "🔴 червона тривога", yellow: "🟡 жовта тривога" };
export const DISCLAIMER = "<i>Прогноз, а не сигнал. Слухайте офіційні тривоги.</i>";
// label, evening threshold (advise remote if P(remote day) > x), morning threshold (call remote if P(clear) < y)
export const STYLE = { careful: ["🛡 Обережно", 0.25, 0.45], balanced: ["⚖️ Баланс", 0.35, 0.35], bold: ["⚡ Сміливо", 0.45, 0.25] };

export const WELCOME = "<b>Привіт! Я прогноз шкільного ранку 🌅</b>\n\n" +
  "О 20:00 напишу, чи буде тривога о 08:00 у вашому районі і чи встигне відбій до контрольної точки школи. " +
  "О 08:05, якщо тривога, підкажу: чекати чи вчитися з дому.\n\nОберіть область 👇 або просто напишіть місто.";
export const HELP = "<b>Команди</b>\n/start · обрати район\n/now · прогноз на завтра\n/settings · налаштування\n" +
  "/pause · пауза на канікули\n/stop · відписатися\n\n" +
  "<blockquote expandable>Як це працює: модель на історії тривог з грудня 2025, перевірена на вересні–жовтні 2026 " +
  "(на 07:00 точніша за найкраще просте правило на 7.8%). Правило МОН: тривога о 08:00 означає дистанційний старт, " +
  "відбій до контрольної точки означає очні уроки. Код і методика: github.com/obodnazar/air-alerts-ua</blockquote>";

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

export const oblastKeyboard = (oblasts) => kb(pairs(oblasts.map((o, i) => [o.replace(" область", "").replace("м. ", ""), `ob:${i}`])));
export const raionKeyboard = (units, index) => kb([...units.map((u) => [[short(u), `u:${index[u]}`]]), [["← Області", "back"]]]);

export function searchKeyboard(hits, index) {
  const seen = new Set(), rows = [];
  for (const [, u] of hits) if (u && !seen.has(u)) { seen.add(u); rows.push([[`📍 ${short(u)}`, `u:${index[u]}`]]); }
  return kb([...rows, [["Обрати зі списку", "back"]]]);
}

export function searchText(hits) {
  const names = [...new Set(hits.filter(([, u]) => u).map(([l]) => esc(l.split(" →")[0].replace(" міська громада", "").replace(" громада", ""))))];
  return "Знайшов 👇" + (names.length ? `\n<i>${names.join(", ")}</i>` : "");
}

export const doneText = (s) => `✅ <b>Готово!</b> Стежу за: ${esc(s.units.map(short).join(", "))}.\n` +
  `Прогноз прийде о <b>${s.evening}</b>. Контрольна точка школи: <b>${String(s.cp).padStart(2, "0")}:00</b>.`;
export const doneKeyboard = () => kb([[["🌙 Прогноз зараз", "now", "primary"]], [["⚙️ Налаштувати", "s:root"]]]);

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
export function bar(p, n = 10) {
  const k = Math.max(0, Math.min(n, Math.round(p * n)));
  return "▰".repeat(k) + "▱".repeat(n - k);
}
const pct = (p) => `${Math.round(p * 100)}%`;
const cpLabel = (cp) => `${String(cp).padStart(2, "0")}:00`;

function eveningBlock(unit, r, cp, style) {
  const pa = r.p_alert_08, pc = r[`p_clear_${cp}`], pr = pa * (1 - pc);
  const advice = pr > STYLE[style][1] ? "Розгляньте дистанційний ранок"
    : pa >= 0.5 ? "Плануйте очний день, але можливий пізніший старт" : "Плануйте очний день";
  return `<b>${esc(short(unit))}</b>\n${mood(pa)} · тривога о 08:00: <b>${pct(pa)}</b>\n` +
    `${bar(pa)} <i>зазвичай ${pct(r.usual)}</i>\n` +
    `Якщо буде, відбій до ${cpLabel(cp)}: ${pct(pc)} · увесь день дистанційно: ${pct(pr)}\n→ <b>${advice}</b>`;
}
function morningBlock(unit, r, cp, style) {
  if (!r) return `<b>${esc(short(unit))}</b>\n🔕 Тривоги немає: очний день за розкладом.`;
  const p = r[`p_clear_${cp}`];
  const advice = p < STYLE[style][2] ? "Оголосіть дистанційний день зараз" : `Чекайте контрольної точки ${cpLabel(cp)}`;
  return `<b>${esc(short(unit))}</b>\n${LEVEL[r.level] || "Тривога"} триває ${Math.round(r.elapsed_min)} хв\n` +
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
export function morningMessage(s, rows, day, banner = "") {
  const units = s.units.filter((u) => rows[u] || s.morning === "always");
  if (!units.length) return null;
  const blocks = units.map((u) => morningBlock(u, rows[u], s.cp, s.style));
  return [`🌅 <b>${day.slice(8, 10)}.${day.slice(5, 7)} · 08:05</b>`, ...blocks, ...tail(s, banner)].join("\n\n");
}
