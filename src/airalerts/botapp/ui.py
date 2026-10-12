"""Message texts and inline keyboards. Pure functions: easy to test, no network."""

from __future__ import annotations

DOW = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"]
LEVEL = {"red": "🔴 червона тривога", "yellow": "🟡 жовта тривога"}
DISCLAIMER = "Прогноз, а не сигнал. Слухайте офіційні тривоги."
# advice thresholds; "balanced" = the values chosen on validation (Aug 2026)
STYLE = {"careful": ("обережний", 0.25, 0.45), "balanced": ("збалансований", 0.35, 0.35), "bold": ("сміливий", 0.45, 0.25)}
#        name        evening: advise remote if P(remote day) > x   morning: call remote if P(all-clear in time) < y

WELCOME = ("Привіт! Я «Шкільний ранок».\n\n"
           "О 20:00 надсилаю прогноз на ранок для вашого району: чи буде тривога о 08:00 і чи встигне відбій до "
           "контрольної точки школи (за правилом МОН). О 08:05, якщо триває тривога, кажу, чекати чи "
           "оголошувати дистанційний день.\n\nОберіть область або просто напишіть назву міста чи району.")
HELP = ("/start: додати район\n/now: прогноз на завтра\n/settings: райони, контрольна точка, час, стиль порад, пауза\n"
        "/stop: відписатися\n\nЯк це рахується: модель на історії тривог з грудня 2025, перевірена на вересні–жовтні 2026. "
        "Код і методика: github.com/obodnazar/air-alerts-ua")


def kb(rows):
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


def oblast_keyboard(oblasts: list[str]):
    names = [(o.replace(" область", "").replace("м. ", ""), f"ob:{i}") for i, o in enumerate(oblasts)]
    return kb([names[i:i + 2] for i in range(0, len(names), 2)])


def raion_keyboard(units: list[str], index: dict):
    btn = [(u.replace(" район", ""), f"u:{index[u]}") for u in units]
    return kb([btn[i:i + 2] for i in range(0, len(btn), 2)] + [[("← до областей", "back")]])


def search_keyboard(hits, index: dict):
    rows = [[(label[:60], f"u:{index[u]}")] for label, u in hits if u]
    return kb(rows + [[("Обрати зі списку областей", "back")]])


def _tick(ok: bool, label: str) -> str:
    return ("✅ " if ok else "") + label


def settings_text(sub: dict) -> str:
    style = STYLE[sub["style"]][0]
    pause = f"\n⏸ Пауза до {sub['paused_until']}" if sub.get("paused_until") else ""
    return (f"⚙️ Налаштування\nРайони: {', '.join(sub['units']) or 'не обрано'}\n"
            f"Контрольна точка: {sub['cp']:02d}:00 · вечірній прогноз о {sub['evening']} · порада: {style}{pause}")


def settings_keyboard(sub: dict):
    return kb([
        [("🏫 Мої райони", "units")],
        [(_tick(sub["cp"] == cp, f"{cp:02d}:00"), f"cp:{cp}") for cp in (9, 10, 11, 12)],
        [(_tick(sub["evening"] == t, f"🌙 {t}"), f"ev:{t}") for t in ("20:00", "20:30", "21:00")],
        [(_tick(sub["style"] == k, v[0]), f"st:{k}") for k, v in STYLE.items()],
        [(_tick(sub["morning"] == "alert", "☀️ лише при тривозі"), "m:alert"), (_tick(sub["morning"] == "always", "☀️ щоранку"), "m:always")],
        [(_tick(bool(sub["banner"]), "📰 новини"), f"bn:{0 if sub['banner'] else 1}"), ("⏸ пауза", "pause")],
    ])


def units_keyboard(units: list[str], index: dict, can_add: bool):
    rows = [[(f"❌ {u}", f"rm:{index[u]}")] for u in units]
    if can_add:
        rows.append([("➕ додати район", "add")])
    return kb(rows + [[("← налаштування", "set")]])


def pause_keyboard():
    return kb([[("1 тиждень", "p:7"), ("2 тижні", "p:14")], [("▶️ відновити", "p:0")], [("← налаштування", "set")]])


def _evening_block(unit: str, row: dict, cp: int, style: str) -> str:
    p_clear = row[f"p_clear_{cp}"]
    p_remote = row["p_alert_08"] * (1 - p_clear)
    advice = "розгляньте дистанційний ранок" if p_remote > STYLE[style][1] else "плануйте очний день"
    return (f"🏫 {unit}\nТривога о 08:00: {row['p_alert_08']:.0%} (зазвичай {row['usual']:.0%})\n"
            f"Якщо буде, відбій до {cp:02d}:00: {p_clear:.0%}\nУвесь день дистанційно: {p_remote:.0%}\n→ {advice}.")


def _morning_block(unit: str, row: dict | None, cp: int, style: str) -> str:
    if row is None:
        return f"🏫 {unit}\nТривоги немає: очний день за розкладом."
    p = row[f"p_clear_{cp}"]
    advice = "оголосіть дистанційний день зараз" if p < STYLE[style][2] else f"чекайте контрольної точки {cp:02d}:00"
    return (f"🏫 {unit}\n{LEVEL.get(row['level'], 'Тривога')} триває {row['elapsed_min']:.0f} хв.\n"
            f"Відбій до {cp:02d}:00: {p:.0%}\n→ {advice}.")


def evening_message(sub: dict, rows: dict, day, banner: str = "") -> str | None:
    blocks = [_evening_block(u, rows[u], sub["cp"], sub["style"]) for u in sub["units"] if u in rows]
    if not blocks:
        return None
    head = f"🌙 Прогноз на {DOW[day.weekday()]} {day:%d.%m}"
    tail = ([banner] if banner and sub["banner"] else []) + [DISCLAIMER]
    return "\n\n".join([head, *blocks, *tail])


def morning_message(sub: dict, rows: dict, day, banner: str = "") -> str | None:
    units = [u for u in sub["units"] if u in rows or sub["morning"] == "always"]
    if not units:
        return None
    blocks = [_morning_block(u, rows.get(u), sub["cp"], sub["style"]) for u in units]
    tail = ([banner] if banner and sub["banner"] else []) + [DISCLAIMER]
    return "\n\n".join([f"☀️ {day:%d.%m} 08:05", *blocks, *tail])


# single-raion helpers kept for the CLI brief and older tests
def evening_text(unit, row, cp, day, banner=""):
    return evening_message(dict(units=[unit], cp=cp, style="balanced", banner=1), {unit: row}, day, banner)


def morning_text(unit, row, cp, day, banner=""):
    return morning_message(dict(units=[unit], cp=cp, style="balanced", banner=1, morning="always"),
                           {unit: row} if row else {}, day, banner)
