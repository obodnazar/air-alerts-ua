"""News banner for the brief (keyword watcher, no LLM).

Reads the latest public posts of three channels and looks for events of the last 18 hours:
  TRUCE, MASS_WARNING, RETALIATION (patterns from events.py).
Lachen alone -> "за повідомленнями ЗМІ, не підтверджено"; also in an official channel -> "підтверджено".
The banner informs; it does not change the numbers (tested in events.py: news adds nothing to the forecast).
"""

from __future__ import annotations

import pandas as pd

from ..events import CLASSES, NOT_NEWS

NEWS = "lachentyt"
OFFICIAL = {"kpszsu": "Повітряні сили", "V_Zelenskiy_official": "Президент"}
TEXT = {
    "TRUCE": "оголошено або обговорюється перемир'я. Прогноз спирається на останні тижні й може бути неточним",
    "MASS_WARNING": "попереджають про можливу масовану атаку. Ночі після таких попереджень у даних бувають масованими "
                    "приблизно в 1.5 раза частіше; цифри нижче цього не враховують",
    "RETALIATION": "Росія погрожує «ударом у відповідь». Цифри нижче цього не враховують",
}


def detect(posts: list[dict], now: pd.Timestamp, hours: int = 18) -> dict:
    """posts: [{channel, published_at, text}] -> {class: set(channels)} for events in the last `hours`."""
    found: dict[str, set] = {}
    for p in posts:
        t = pd.Timestamp(p["published_at"])
        if not (now - pd.Timedelta(hours=hours) <= t <= now):
            continue
        low = p["text"].lower()
        if NOT_NEWS.search(low):
            continue
        for cls, rx in CLASSES.items():
            if rx.search(low):
                found.setdefault(cls, set()).add(p["channel"])
    return found


def render(found: dict) -> str:
    lines = []
    for cls in ("TRUCE", "MASS_WARNING", "RETALIATION"):
        if cls not in found:
            continue
        official = [OFFICIAL[c] for c in found[cls] if c in OFFICIAL]
        status = f"підтверджено: {', '.join(official)}" if official else "за повідомленнями ЗМІ, не підтверджено офіційно"
        lines.append(f"⚠️ {TEXT[cls]} ({status}).")
    return "\n".join(lines)


def fetch(now: pd.Timestamp) -> str:
    from ..sources.telegram import fetch_page, parse_page

    posts = []
    for ch in [NEWS, *OFFICIAL]:
        try:
            posts += parse_page(fetch_page(ch, None), ch)
        except Exception:  # a channel being down must not stop the brief
            continue
    return render(detect(posts, now))
