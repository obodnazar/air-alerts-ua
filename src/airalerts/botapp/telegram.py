"""Telegram Bot API over plain HTTP. Retries on 429 (waits retry_after), reports 403 (user blocked the bot)."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass


class Blocked(Exception):
    """403: the user blocked the bot or deleted the chat."""


class Telegram:
    def __init__(self, token: str | None = None, opener=urllib.request.urlopen, sleep=time.sleep):
        self.token = token or os.environ["TELEGRAM_BOT_TOKEN"]
        self.opener, self.sleep = opener, sleep

    def call(self, method: str, **params):
        data = urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, (dict, list)) else v
                                       for k, v in params.items() if v is not None}).encode()
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        for attempt in range(5):
            try:
                with self.opener(url, data=data, timeout=70) as r:
                    return json.load(r)["result"]
            except urllib.error.HTTPError as e:
                body = json.loads(e.read() or b"{}")
                if e.code == 429:
                    self.sleep(body.get("parameters", {}).get("retry_after", 2 ** attempt))
                    continue
                if e.code == 403:
                    raise Blocked(body.get("description", "forbidden")) from e
                if e.code == 400 and "message is not modified" in body.get("description", ""):
                    return None
                raise
            except (urllib.error.URLError, TimeoutError):
                self.sleep(2 ** attempt)
        raise RuntimeError(f"{method}: gave up after retries")

    # thin helpers used by the router
    def send(self, chat_id, text, keyboard=None):
        return self.call("sendMessage", chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML",
                         link_preview_options={"is_disabled": True})

    def edit(self, chat_id, message_id, text, keyboard=None):
        return self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard,
                         parse_mode="HTML", link_preview_options={"is_disabled": True})

    def react(self, chat_id, message_id, emoji="👀"):
        """Acknowledge a typed message with a reaction instead of an extra message (nice-to-have: never fails)."""
        try:
            return self.call("setMessageReaction", chat_id=chat_id, message_id=message_id,
                             reaction=[{"type": "emoji", "emoji": emoji}])
        except Exception:
            return None

    def answer(self, callback_id, text=None):
        return self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)
