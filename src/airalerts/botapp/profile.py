"""Set the bot's public profile: name, descriptions, commands and avatar (setMyProfilePhoto, Bot API 9.4).

    export TELEGRAM_BOT_TOKEN=...
    PYTHONPATH=src python -m airalerts.botapp.profile --name "Dawnly · Шкільний ранок"
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
import uuid
from pathlib import Path

from .main import COMMANDS
from .telegram import Telegram

SHORT = "Прогноз шкільного ранку: чи буде тривога о 08:00 і чи встигне відбій до уроків. Щовечора о 20:00."
LONG = ("Щовечора о 20:00 бот пише для вашого району, чи буде тривога о 08:00 і чи встигне відбій до контрольної "
        "точки школи (правило МОН). О 08:05, якщо тривога, підкаже: чекати чи вчитися з дому.\n\n"
        "Модель перевірена на вересні–жовтні 2026. Це прогноз, а не сигнал: слухайте офіційні тривоги.")


def set_photo(token: str, path: Path) -> dict:
    """multipart/form-data upload with the standard library only."""
    boundary = uuid.uuid4().hex
    photo = json.dumps({"type": "static", "photo": "attach://avatar"})
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"\r\n\r\n{photo}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"avatar\"; filename=\"avatar.png\"\r\n"
            f"Content-Type: image/png\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/setMyProfilePhoto", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="Шкільний ранок")
    ap.add_argument("--photo", type=Path, default=Path("brand/avatar.png"))
    a = ap.parse_args()
    tg = Telegram()
    tg.call("setMyName", name=a.name)
    tg.call("setMyShortDescription", short_description=SHORT)
    tg.call("setMyDescription", description=LONG)
    tg.call("setMyCommands", commands=COMMANDS)
    print("photo:", set_photo(os.environ["TELEGRAM_BOT_TOKEN"], a.photo).get("ok"))
    print("profile updated")


if __name__ == "__main__":
    main()
