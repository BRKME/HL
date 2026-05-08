"""Telegram Bot API. Один канал, HTML parse mode, авто-split на >4000 символов."""
from __future__ import annotations

import os
import time

import requests

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
OWNER_CHAT_ID = os.environ.get("TELEGRAM_OWNER_CHAT_ID", "")

_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def _send(chat_id: str, text: str, parse_mode: str = "HTML") -> None:
    r = requests.post(
        f"{_API}/sendMessage",
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
        _send(chat_id, text, parse_mode)
        return
    if r.status_code >= 400:
        body = r.text[:500]
        snippet = text[:300].replace("\n", "\\n")
        print(f"Telegram {r.status_code}: {body}\nFirst 300 chars: {snippet}", flush=True)
    r.raise_for_status()


def send_messages(messages: list[str]) -> None:
    for i, text in enumerate(messages):
        if not text:
            continue
        _send(CHAT_ID, text)
        if i < len(messages) - 1:
            time.sleep(1.5)


def alert_owner(html_text: str) -> None:
    target = OWNER_CHAT_ID or CHAT_ID
    _send(target, html_text, parse_mode="HTML")
