"""Message texts and inline keyboards. Pure functions: easy to test, no network."""

from __future__ import annotations

DOW = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"]
LEVEL = {"red": "🔴 червона тривога", "yellow": "🟡 жовта тривога"}
DISCLAIMER = "Прогноз, а не сигнал. Слухайте офіційні тривоги."
TAU_REMOTE_08 = 0.35  # threshold chosen on validation (Aug 2026) for "call the remote day now"

WELCOME = ("Привіт! Я «Шкільний ранок».\n\n"
           "О 20:00 надсилаю прогноз на ранок для вашого району: чи буде тривога о 08:00 і чи встигне відбій до "
           "контрольної точки школи (за правилом МОН). О 08:05, якщо триває тривога, кажу, чекати чи "
           "оголошувати дистанційний день.\n\nОберіть область або просто напишіть назву міста чи району.")
HELP = ("/start: обрати район\n/now: прогноз на завтра зараз\n/settings: контрольна точка і ранкові повідомлення\n"
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


def settings_keyboard(sub: dict):
    cps = [(("✅ " if sub["cp"] == cp else "") + f"{cp:02d}:00", f"cp:{cp}") for cp in (9, 10, 11, 12)]
    m = sub["morning"]
    return kb([cps, [(("✅ " if m == "alert" else "") + "лише якщо тривога", "m:alert"),
                     (("✅ " if m == "always" else "") + "завжди", "m:always")]])


def evening_text(unit: str, row: dict, cp: int, day, banner: str = "") -> str:
    p_clear = row[f"p_clear_{cp}"]
    p_remote = row["p_alert_08"] * (1 - p_clear)
    advice = "розгляньте дистанційний ранок" if p_remote > 0.35 else "плануйте очний день"
    lines = [f"🏫 {unit} · {DOW[day.weekday()]} {day:%d.%m}",
             f"Тривога о 08:00: {row['p_alert_08']:.0%} (зазвичай {row['usual']:.0%})",
             f"Якщо буде, відбій до {cp:02d}:00: {p_clear:.0%}",
             f"Увесь день дистанційно: {p_remote:.0%}",
             f"→ {advice}."]
    if banner:
        lines += ["", banner]
    return "\n".join(lines + ["", DISCLAIMER])


def morning_text(unit: str, row: dict | None, cp: int, day, banner: str = "") -> str:
    if row is None:
        lines = [f"🏫 {unit} · {day:%d.%m} 08:05", "Тривоги немає: очний день за розкладом."]
    else:
        p = row[f"p_clear_{cp}"]
        advice = "оголосіть дистанційний день зараз" if p < TAU_REMOTE_08 else f"чекайте контрольної точки {cp:02d}:00"
        lines = [f"🏫 {unit} · {day:%d.%m} 08:05",
                 f"{LEVEL.get(row['level'], 'Тривога')} триває {row['elapsed_min']:.0f} хв.",
                 f"Відбій до {cp:02d}:00: {p:.0%}", f"→ {advice}."]
    if banner:
        lines += ["", banner]
    return "\n".join(lines + ["", DISCLAIMER])
