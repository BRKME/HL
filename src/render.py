"""Рендер еженедельного отчёта. Telegram HTML parse mode."""
from __future__ import annotations

import html
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_MOSCOW = timezone(timedelta(hours=3))


def _e(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=False)


_RU_MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _ru_date(dt: datetime) -> str:
    return f"{dt.day} {_RU_MONTHS[dt.month - 1]} {dt.year}"


def _week_window(dt: datetime) -> str:
    end = dt + timedelta(days=6)
    return f"{dt.day}–{end.day} {_RU_MONTHS[dt.month - 1]}"


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(disabled_extensions=("html", "j2")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["e"] = _e
    return env


def render_report(*, signal: dict, picks: list[dict], skipped: list[dict]) -> str:
    """Один HTML-документ для Telegram. Должен укладываться в ~3800 символов."""
    env = _env()
    now = datetime.now(_MOSCOW)
    ctx = {
        "date_str": _ru_date(now),
        "week_window": _week_window(now),
        "signal_type": signal["signal"],
        "leverage": signal["leverage"],
        "reasons": signal.get("reasons") or [],
        "raw": signal.get("raw") or {},
        "picks": picks,
        "skipped": skipped,
        "signal_emoji": _signal_emoji(signal["signal"]),
        "signal_label": _signal_label(signal["signal"], signal["leverage"]),
    }

    if signal["signal"] in ("SKIP", "EXIT"):
        tpl = "report_skip.html.j2"
    else:
        tpl = "report_buy.html.j2"
    return env.get_template(tpl).render(**ctx).strip()


def _signal_emoji(sig: str) -> str:
    return {
        "STRONG": "🟢",
        "MODERATE": "🟡",
        "SKIP": "⚪️",
        "EXIT": "🔴",
    }.get(sig, "⚪️")


def _signal_label(sig: str, lev: int) -> str:
    if sig == "STRONG":
        return f"СИЛЬНЫЙ (плечо {lev}×)"
    if sig == "MODERATE":
        return f"УМЕРЕННЫЙ (плечо {lev}×)"
    if sig == "SKIP":
        return "ПРОПУСК (не покупаем)"
    if sig == "EXIT":
        return "ВЫХОД (закрыть позиции)"
    return sig
