"""Texts and keyboards (v3, mobile-first). Pure functions: no network.

Design rules (docs/research/10-bot-ux.md, checked against core.telegram.org):
- a button label fits a phone: ≤ ~14 characters when two share a row, otherwise one per row;
- the verdict comes first, details go into an expandable quote;
- risk = word + weather shape (☀️ ⛅ 🌧), never colour alone; red/yellow stay reserved for real alert levels;
- button `style` only "primary" (blue) / "success" (green) for the main action, never "danger".
"""

from __future__ import annotations

from html import escape as _escape


def escape(text: str) -> str:
    return _escape(text, quote=False)  # apostrophes stay readable; < > & are still escaped

DOW = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"]
LEVEL = {"red": "🔴 червона тривога", "yellow": "🟡 жовта тривога"}
DISCLAIMER = "<i>Прогноз, а не сигнал. Слухайте офіційні тривоги.</i>"
STYLE = {"careful": ("🛡 Обережно", 0.25, 0.45), "balanced": ("⚖️ Баланс", 0.35, 0.35), "bold": ("⚡ Сміливо", 0.45, 0.25)}
#        label          evening: advise remote if P(remote day) > x   morning: call remote if P(all-clear in time) < y

WELCOME = ("<b>Привіт! Я прогноз шкільного ранку 🌅</b>\n\n"
           "О 20:00 напишу, чи буде тривога о 08:00 у вашому районі і чи встигне відбій до контрольної точки школи. "
           "О 08:05, якщо тривога, підкажу: чекати чи вчитися з дому.\n\n"
           "Оберіть область 👇 або просто напишіть місто.")
HELP = ("<b>Команди</b>\n/start · обрати район\n/now · прогноз на завтра\n/settings · налаштування\n"
        "/pause · пауза на канікули\n/stop · відписатися\n\n"
        "<blockquote expandable>Як це працює: модель на історії тривог з грудня 2025, перевірена на вересні–жовтні 2026 "
        "(на 07:00 точніша за найкраще просте правило на 7.8%). Правило МОН: тривога о 08:00 означає дистанційний старт, "
        "відбій до контрольної точки означає очні уроки. Код і методика: github.com/obodnazar/air-alerts-ua</blockquote>")


def btn(text, data, style=None):
    b = {"text": text, "callback_data": data}
    if style in ("primary", "success"):
        b["style"] = style
    return b


def kb(rows):
    return {"inline_keyboard": [[btn(*b) if isinstance(b, tuple) else b for b in row] for row in rows]}


def _pairs(buttons, limit=14):
    """Two per row only when both labels are short enough for a phone; otherwise one per row."""
    rows, i = [], 0
    while i < len(buttons):
        if i + 1 < len(buttons) and len(buttons[i][0]) <= limit and len(buttons[i + 1][0]) <= limit:
            rows.append([buttons[i], buttons[i + 1]])
            i += 2
        else:
            rows.append([buttons[i]])
            i += 1
    return rows


def short(unit: str) -> str:
    return unit.replace(" район", "")


def oblast_keyboard(oblasts: list[str]):
    names = [(o.replace(" область", "").replace("м. ", ""), f"ob:{i}") for i, o in enumerate(oblasts)]
    return kb(_pairs(names))


def raion_keyboard(units: list[str], index: dict):
    return kb([[(short(u), f"u:{index[u]}")] for u in units] + [[("← Області", "back")]])


def search_keyboard(hits, index: dict):
    seen, rows = set(), []
    for _, u in hits:
        if u and u not in seen:
            seen.add(u)
            rows.append([(f"📍 {short(u)}", f"u:{index[u]}")])
    return kb(rows + [[("Обрати зі списку", "back")]])


def search_text(hits) -> str:
    names = [escape(label.split(" →")[0].replace(" міська громада", "").replace(" громада", "")) for label, u in hits if u]
    return "Знайшов 👇" + (f"\n<i>{', '.join(dict.fromkeys(names))}</i>" if names else "")


def done_text(sub: dict) -> str:
    return (f"✅ <b>Готово!</b> Стежу за: {escape(', '.join(map(short, sub['units'])))}.\n"
            f"Прогноз прийде о <b>{sub['evening']}</b>. Контрольна точка школи: <b>{sub['cp']:02d}:00</b>.")


def done_keyboard():
    return kb([[("🌙 Прогноз зараз", "now", "primary")], [("⚙️ Налаштувати", "s:root")]])


# ---------- settings: a menu with current values, one row each; tap → its own screen ----------

def settings_text(sub: dict) -> str:
    pause = f"\n⏸ Пауза до {sub['paused_until']}" if sub.get("paused_until") else ""
    return f"⚙️ <b>Налаштування</b>{pause}"


def settings_keyboard(sub: dict):
    m = "при тривозі" if sub["morning"] == "alert" else "щоранку"
    return kb([
        [(f"🏫 Райони · {len(sub['units'])}", "s:units")],
        [(f"🔔 Контрольна точка · {sub['cp']:02d}:00", "s:cp")],
        [(f"🌙 Прогноз о · {sub['evening']}", "s:ev")],
        [(f"🎚 Поради · {STYLE[sub['style']][0].split()[1]}", "s:st")],
        [(f"☀️ Ранок · {m}", "s:m")],
        [(f"📰 Новини · {'так' if sub['banner'] else 'ні'}", "s:bn")],
        [("⏸ Пауза", "s:pause")],
    ])


SCREENS = {
    "cp": ("🔔 <b>Контрольна точка школи</b>\nДо цієї години має прийти відбій, щоб діти пішли на очні уроки (правило МОН).",
           lambda s: [[(("✓ " if s["cp"] == c else "") + f"{c:02d}:00", f"cp:{c}") for c in (9, 10, 11, 12)]]),
    "ev": ("🌙 <b>Коли надсилати прогноз</b>",
           lambda s: [[(("✓ " if s["evening"] == t else "") + t, f"ev:{t}") for t in ("20:00", "20:30", "21:00")]]),
    "st": ("🎚 <b>Як охоче радити дистанційний ранок</b>\nБаланс: пороги, перевірені на даних.",
           lambda s: [[(("✓ " if s["style"] == k else "") + v[0], f"st:{k}")] for k, v in STYLE.items()]),
    "m": ("☀️ <b>Ранкове повідомлення о 08:05</b>",
          lambda s: [[(("✓ " if s["morning"] == "alert" else "") + "🔔 При тривозі", "m:alert"),
                      (("✓ " if s["morning"] == "always" else "") + "📅 Щоранку", "m:always")]]),
    "bn": ("📰 <b>Банер новин</b>\nПро перемир'я чи попередження про масовану атаку. Цифри прогнозу він не змінює.",
           lambda s: [[(("✓ " if s["banner"] else "") + "Так", "bn:1"), (("✓ " if not s["banner"] else "") + "Ні", "bn:0")]]),
    "pause": ("⏸ <b>Пауза</b>\nНа канікули чи хворобу, щоб я не писав.",
              lambda s: [[("1 тиждень", "p:7"), ("2 тижні", "p:14")], [("▶️ Відновити", "p:0")]]),
}


def screen(name: str, sub: dict):
    text, rows = SCREENS[name]
    return text, kb(rows(sub) + [[("← Назад", "s:root")]])


def units_keyboard(units: list[str], index: dict, can_add: bool):
    rows = [[(f"✕ {short(u)}", f"rm:{index[u]}")] for u in units]
    if can_add:
        rows.append([("＋ Додати район", "add", "primary")])
    return kb(rows + [[("← Назад", "s:root")]])


def pause_keyboard():
    return screen("pause", {})[1]


# ---------- forecasts ----------

def mood(p_alert: float) -> str:
    if p_alert < 0.2:
        return "☀️ Спокійний ранок"
    if p_alert < 0.5:
        return "⛅ Можлива тривога"
    return "🌧 Тривожний ранок"


def bar(p: float, n: int = 10) -> str:
    k = max(0, min(n, round(p * n)))
    return "▰" * k + "▱" * (n - k)


def _evening_block(unit: str, row: dict, cp: int, style: str) -> str:
    p_alert, p_clear = row["p_alert_08"], row[f"p_clear_{cp}"]
    p_remote = p_alert * (1 - p_clear)
    if p_remote > STYLE[style][1]:
        advice = "Розгляньте дистанційний ранок"
    elif p_alert >= 0.5:  # likely alert, but the all-clear will probably come in time: say both
        advice = "Плануйте очний день, але можливий пізніший старт"
    else:
        advice = "Плануйте очний день"
    return (f"<b>{escape(short(unit))}</b>\n{mood(p_alert)} · тривога о 08:00: <b>{p_alert:.0%}</b>\n"
            f"{bar(p_alert)} <i>зазвичай {row['usual']:.0%}</i>\n"
            f"Якщо буде, відбій до {cp:02d}:00: {p_clear:.0%} · увесь день дистанційно: {p_remote:.0%}\n"
            f"→ <b>{advice}</b>")


def _morning_block(unit: str, row: dict | None, cp: int, style: str) -> str:
    if row is None:
        return f"<b>{escape(short(unit))}</b>\n☀️ Тривоги немає: очний день за розкладом."
    p = row[f"p_clear_{cp}"]
    advice = "оголосіть дистанційний день зараз" if p < STYLE[style][2] else f"чекайте контрольної точки {cp:02d}:00"
    return (f"<b>{escape(short(unit))}</b>\n{LEVEL.get(row['level'], 'Тривога')} триває {row['elapsed_min']:.0f} хв\n"
            f"{bar(p)} відбій до {cp:02d}:00: <b>{p:.0%}</b>\n→ <b>{advice[0].upper() + advice[1:]}</b>")


def _tail(sub, banner):
    return ([escape(banner)] if banner and sub["banner"] else []) + [DISCLAIMER]


def evening_message(sub: dict, rows: dict, day, banner: str = "") -> str | None:
    blocks = [_evening_block(u, rows[u], sub["cp"], sub["style"]) for u in sub["units"] if u in rows]
    if not blocks:
        return None
    head = f"🌙 <b>Прогноз на {DOW[day.weekday()]} {day:%d.%m}</b>"
    how = ("<blockquote expandable>Тривога о 08:00 означає дистанційний старт; відбій до контрольної точки означає очні "
           "уроки (МОН, лист №1/19575). «Зазвичай» означає частку ранків з тривогою за останні 28 днів.</blockquote>")
    return "\n\n".join([head, *blocks, how, *_tail(sub, banner)])


def morning_message(sub: dict, rows: dict, day, banner: str = "") -> str | None:
    units = [u for u in sub["units"] if u in rows or sub["morning"] == "always"]
    if not units:
        return None
    blocks = [_morning_block(u, rows.get(u), sub["cp"], sub["style"]) for u in units]
    return "\n\n".join([f"🌅 <b>{day:%d.%m} · 08:05</b>", *blocks, *_tail(sub, banner)])


def evening_text(unit, row, cp, day, banner=""):
    return evening_message(dict(units=[unit], cp=cp, style="balanced", banner=1), {unit: row}, day, banner)


def morning_text(unit, row, cp, day, banner=""):
    return morning_message(dict(units=[unit], cp=cp, style="balanced", banner=1, morning="always"),
                           {unit: row} if row else {}, day, banner)
