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


def _fmt_price(p: float | None) -> str:
    """Цена в формате под порядок: BTC=98500, ETH=3420, PEPE=0.000012."""
    if p is None:
        return "—"
    if p >= 1000:
        return f"{p:,.0f}".replace(",", " ")
    if p >= 10:
        return f"{p:.2f}"
    if p >= 0.01:
        return f"{p:.4f}"
    return f"{p:.8f}".rstrip("0").rstrip(".")


def _humanize_skip_reason(reason: str) -> str:
    """Технические skip-причины → человеческий русский."""
    if not reason:
        return ""
    r = reason
    # RSI overheat
    if "RSI" in r and "перегрет" in r:
        return r.replace("RSI(D1)", "RSI на дневке")
    # funding
    if "funding" in r.lower():
        # "funding +84% APR > 60%" → "ставка фандинга +84% годовых — слишком дорого"
        import re
        m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%\s*APR", r)
        if m:
            return f"ставка фандинга {m.group(1)}% годовых — слишком дорого"
        return r
    # ниже EMA200
    if "EMA200" in r:
        return "цена ниже долгосрочного тренда"
    # не листится
    if "не листится" in r:
        return "не торгуется на Hyperliquid"
    # мало истории
    if "мало истории" in r:
        return "недостаточно данных для анализа"
    # candles error
    if "HL candles error" in r:
        return "ошибка получения данных с биржи"
    # fallback
    return r


_HEADLINES = {
    "STRONG": "🎯 Сильный сигнал — покупаем",
    "MODERATE": "🎯 Умеренный сигнал — покупаем осторожно",
    "MODERATE_DEFENSIVE": "🛡 Защитная покупка — берём только BTC",
    "SKIP": "⚪ Эту неделю пропускаем",
    "EXIT": "🔴 Выходим из позиций",
}


def render_report(*, signal: dict, picks: list[dict], skipped: list[dict]) -> str:
    """Один HTML-документ для Telegram. Должен укладываться в ~3800 символов."""
    env = _env()
    now = datetime.now(_MOSCOW)

    # Форматирование цен и SL для каждого pick'а
    picks_fmt = []
    for p in picks:
        picks_fmt.append({
            **p,
            "entry_fmt": _fmt_price(p.get("entry")),
            "sl_fmt": _fmt_price(p.get("sl_price")),
        })

    # Перевод skip-причин на человеческий
    skipped_fmt = [
        {**s, "reason_human": _humanize_skip_reason(s.get("reason", ""))}
        for s in skipped
    ]

    # Headline зависит от сигнала + defensive-флага
    sig = signal["signal"]
    if sig == "MODERATE" and signal.get("defensive"):
        headline = _HEADLINES["MODERATE_DEFENSIVE"]
    else:
        headline = _HEADLINES.get(sig, sig)

    ctx = {
        "date_str": _ru_date(now),
        "week_window": _week_window(now),
        "signal_type": sig,
        "leverage": signal["leverage"],
        "reasons": signal.get("reasons") or [],
        "raw": signal.get("raw") or {},
        "picks": picks_fmt,
        "skipped": skipped_fmt,
        "signal_emoji": _signal_emoji(sig),
        "signal_label": _signal_label(sig, signal["leverage"]),
        "headline": headline,
    }

    if sig in ("SKIP", "EXIT"):
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
