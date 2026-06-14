"""Telegram Bot API. Один канал, HTML parse mode, авто-split на >4000 символов.

Env vars (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, optional TELEGRAM_OWNER_CHAT_ID)
are read LAZILY at call time, not at import time. This way:
- Tests can import this module without setting the env (CI doesn't need it)
- Other modules that import this transitively don't crash if TG isn't
  configured (e.g. a code-path that never actually sends a message)

If a send is attempted without TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID set,
we raise a clear RuntimeError instead of a cryptic KeyError on import.
"""
from __future__ import annotations

import os
import time

import requests


class TelegramConfigError(RuntimeError):
    """Raised when send is attempted but env vars aren't configured."""


def _read_env() -> tuple[str, str, str]:
    """Return (bot_token, chat_id, owner_chat_id). Raises on missing required."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    owner = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "")
    if not bot_token or not chat_id:
        raise TelegramConfigError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set to send messages"
        )
    return bot_token, chat_id, owner


def _api_url(bot_token: str) -> str:
    return f"https://api.telegram.org/bot{bot_token}"


def _send(bot_token: str, chat_id: str, text: str, parse_mode: str = "HTML") -> None:
    r = requests.post(
        f"{_api_url(bot_token)}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if r.status_code == 429:
        retry_after = int(r.json().get("parameters", {}).get("retry_after", 5))
        time.sleep(retry_after + 1)
        _send(bot_token, chat_id, text, parse_mode)
        return
    if r.status_code >= 400:
        body = r.text[:500]
        snippet = text[:300].replace("\n", "\\n")
        print(f"Telegram {r.status_code}: {body}\nFirst 300 chars: {snippet}", flush=True)
    r.raise_for_status()


_LAST_SEND_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state", "last_channel_send.txt")


def _mark_sent() -> None:
    """Отметить факт отправки в канал (для heartbeat: шлёт только в тишину)."""
    try:
        import datetime as _dt
        os.makedirs(os.path.dirname(_LAST_SEND_PATH), exist_ok=True)
        with open(_LAST_SEND_PATH, "w") as f:
            f.write(_dt.datetime.now(_dt.timezone.utc).isoformat())
    except Exception:
        pass


def send_messages(messages: list[str]) -> None:
    """Send each message in sequence with 1.5s pause between."""
    bot_token, chat_id, _ = _read_env()
    sent_any = False
    for i, text in enumerate(messages):
        if not text:
            continue
        _send(bot_token, chat_id, text)
        sent_any = True
        if i < len(messages) - 1:
            time.sleep(1.5)
    if sent_any:
        _mark_sent()


def alert_owner(html_text: str) -> None:
    """Send to the owner chat if configured, else to the main chat."""
    bot_token, chat_id, owner = _read_env()
    target = owner or chat_id
    _send(bot_token, target, html_text, parse_mode="HTML")
