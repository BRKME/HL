"""Telegram renderer for whale signals.

Two modes, each producing a single HTML message:
- instant: warn/critical signals — sent immediately after each whale-monitor run
- digest:  info signals accumulated over ~24h — sent once a day

Both modes keep the message under Telegram's 4096-char limit, escape HTML,
and use markers consistent with daily_monitor (🐋 for whale-related lines).
"""
from __future__ import annotations

import html
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from src.whale_correlation import (
    Signal,
    SIG_CLUSTER, SIG_OVERLAP, SIG_NEW_OPEN, SIG_FLIP,
    SEV_INFO, SEV_WARN, SEV_CRITICAL,
)


_MOSCOW = timezone(timedelta(hours=3))
_TG_LIMIT = 4096
_MAX_LINES_PER_SECTION = 15

_RU_MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _e(s) -> str:
    return html.escape("" if s is None else str(s), quote=False)


def _short_whale(addr: str) -> str:
    if not addr or len(addr) < 10:
        return addr or "?"
    return addr[:8] + "…"


def _ru_date(dt: datetime) -> str:
    return f"{dt.day} {_RU_MONTHS[dt.month - 1]}"


def _fmt_money(v: float) -> str:
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}k"
    return f"${v:.0f}"


# --------------------------------------------------------------- routing

def split_by_mode(signals: list[Signal]) -> tuple[list[Signal], list[Signal]]:
    """Return (instant, digest). warn+ are instant; info are digest."""
    instant: list[Signal] = []
    digest: list[Signal] = []
    for s in signals:
        if s.severity >= SEV_WARN:
            instant.append(s)
        else:
            digest.append(s)
    return instant, digest


# --------------------------------------------------------------- instant

def _format_cluster(s: Signal) -> str:
    d = s.details
    coin = _e(s.coin)
    side = (d.get("direction") or "").upper()
    n = d.get("whale_count", 0)
    marker = "🎯⚡" if d.get("focus") else "⚡"
    return f"{marker} <b>CLUSTER {coin}</b> {side} — {n} китов"


def _format_flip(s: Signal) -> str:
    d = s.details
    coin = _e(s.coin)
    frm = (d.get("from_side") or "").upper()
    to = (d.get("to_side") or "").upper()
    whale = _e(_short_whale(d.get("whale", "")))
    notional = d.get("notional_usd")
    notional_str = f" • {_fmt_money(float(notional))}" if notional else ""
    marker = "🎯🔄" if d.get("focus") else "🔄"
    return f"{marker} <b>FLIP {coin}</b> {frm} → {to} • <code>{whale}</code>{notional_str}"


def _format_focus_new_open(s: Signal) -> str:
    """Phase 3.2 new path: focus NEW_OPEN is warn-severity, gets its own line."""
    d = s.details
    coin = _e(s.coin)
    direction = (d.get("direction") or "").upper()
    whale = _e(_short_whale(d.get("whale", "")))
    notional = d.get("notional_usd", 0)
    wr = d.get("winrate_used", 0)
    return (f"🎯 <b>NEW_OPEN {coin}</b> {direction} • "
            f"<code>{whale}</code> • {_fmt_money(notional)} (WR {wr:.0%})")


def render_instant_alerts(signals: list[Signal], now: datetime) -> Optional[str]:
    """Build the immediate alert message for warn/critical signals.

    Returns None when there's nothing to send.
    """
    if not signals:
        return None

    msk = now.astimezone(_MOSCOW)
    lines = [f"🐋 <b>Whale watch</b> — {_ru_date(msk)}, {msk.strftime('%H:%M')} MSK"]

    # sort: highest severity first, stable inside tier
    ordered = sorted(signals, key=lambda s: -s.severity)
    for s in ordered:
        if s.rule == SIG_CLUSTER:
            lines.append(_format_cluster(s))
        elif s.rule == SIG_FLIP:
            lines.append(_format_flip(s))
        elif s.rule == SIG_NEW_OPEN:
            lines.append(_format_focus_new_open(s))
        else:
            # any other warn-level rule — fallback to message field
            lines.append(f"• {_e(s.message)}")

    msg = "\n".join(lines)
    if len(msg) > _TG_LIMIT:
        # extreme defensive cap; in practice instant lists are tiny
        msg = msg[: _TG_LIMIT - 20] + "\n… (truncated)"
    return msg


# --------------------------------------------------------------- digest

def _digest_overlap_section(signals: list[Signal]) -> Optional[str]:
    if not signals:
        return None
    # dedup by (coin, whale): one line per pair, with count
    grouped: dict[tuple[str, str], list[Signal]] = defaultdict(list)
    for s in signals:
        whale = s.details.get("whale", "")
        grouped[(s.coin, whale)].append(s)

    # sort sections by count descending, then by max winrate
    ranked = sorted(
        grouped.items(),
        key=lambda kv: (-len(kv[1]), -max((x.details.get("winrate_used", 0) for x in kv[1]), default=0)),
    )
    lines = ["", "<b>👥 Совпадения с твоими позициями</b>"]
    for (coin, whale), group in ranked[:_MAX_LINES_PER_SECTION]:
        n = len(group)
        wr = max((x.details.get("winrate_used", 0) for x in group), default=0)
        whale_short = _e(_short_whale(whale)) if whale else "?"
        suffix = f" ×{n}" if n > 1 else ""
        lines.append(
            f"• <code>{_e(coin)}</code> от <code>{whale_short}</code> "
            f"(WR {wr:.0%}){suffix}"
        )
    if len(ranked) > _MAX_LINES_PER_SECTION:
        lines.append(f"  …и ещё {len(ranked) - _MAX_LINES_PER_SECTION}")
    return "\n".join(lines)


def _digest_new_open_section(signals: list[Signal]) -> Optional[str]:
    if not signals:
        return None
    # group by coin only — multiple whales opening same coin is the signal
    by_coin: dict[str, list[Signal]] = defaultdict(list)
    for s in signals:
        by_coin[s.coin].append(s)

    # sort sections by count descending
    ranked = sorted(by_coin.items(), key=lambda kv: -len(kv[1]))

    lines = ["", "<b>🆕 Новые входы китов</b>"]
    for coin, group in ranked[:_MAX_LINES_PER_SECTION]:
        n = len(group)
        total_notional = sum(g.details.get("notional_usd", 0) for g in group)
        directions = Counter(g.details.get("direction", "?") for g in group)
        # majority direction
        majority_dir = directions.most_common(1)[0][0].upper() if directions else "?"
        suffix = f" ×{n}" if n > 1 else ""
        lines.append(
            f"• <code>{_e(coin)}</code> {majority_dir} • "
            f"{_fmt_money(total_notional)}{suffix}"
        )
    if len(ranked) > _MAX_LINES_PER_SECTION:
        lines.append(f"  …и ещё {len(ranked) - _MAX_LINES_PER_SECTION}")
    return "\n".join(lines)


def _digest_rank_section(signals: list[Signal]) -> Optional[str]:
    """Render NEW_ENTRANT / DROP_OFF block in digest."""
    new_entrants = [s for s in signals if s.rule == "WHALE_NEW_ENTRANT"]
    drop_offs = [s for s in signals if s.rule == "WHALE_DROP_OFF"]
    if not new_entrants and not drop_offs:
        return None

    lines = ["", "<b>📊 Изменения в топе</b>"]
    for s in new_entrants[:_MAX_LINES_PER_SECTION]:
        whale = _e(_short_whale(s.details.get("whale", "")))
        rank = s.details.get("last_rank", "?")
        runs = s.details.get("consecutive_in_top", 0)
        lines.append(f"🆕 <code>{whale}</code> вошёл в топ (rank {rank}, {runs} запусков подряд)")
    for s in drop_offs[:_MAX_LINES_PER_SECTION]:
        whale = _e(_short_whale(s.details.get("whale", "")))
        runs = s.details.get("runs_in_top", 0)
        last_rank = s.details.get("last_rank", "?")
        lines.append(f"📉 <code>{whale}</code> ушёл из топа (был {runs} запусков, последний rank {last_rank})")
    return "\n".join(lines)


def render_digest(signals: list[Signal], now: datetime) -> Optional[str]:
    """Build the daily digest for info-level signals."""
    if not signals:
        return None

    overlap = [s for s in signals if s.rule == SIG_OVERLAP]
    new_open = [s for s in signals if s.rule == SIG_NEW_OPEN]
    rank_signals = [s for s in signals if s.rule in ("WHALE_NEW_ENTRANT", "WHALE_DROP_OFF")]

    msk = now.astimezone(_MOSCOW)
    parts = [
        f"🐋 <b>Whale digest за 24ч</b> — {_ru_date(msk)}, {msk.strftime('%H:%M')} MSK",
        f"Всего сигналов: {len(signals)}",
    ]

    block = _digest_overlap_section(overlap)
    if block:
        parts.append(block)
    block = _digest_new_open_section(new_open)
    if block:
        parts.append(block)
    block = _digest_rank_section(rank_signals)
    if block:
        parts.append(block)

    # other info-level rules — generic fallback
    other = [s for s in signals if s.rule not in (
        SIG_OVERLAP, SIG_NEW_OPEN, "WHALE_NEW_ENTRANT", "WHALE_DROP_OFF",
    )]
    if other:
        parts.append("\n<b>Прочее</b>")
        for s in other[:_MAX_LINES_PER_SECTION]:
            parts.append(f"• {_e(s.message)}")

    msg = "\n".join(parts)
    if len(msg) > _TG_LIMIT:
        while len(msg) > _TG_LIMIT and len(parts) > 2:
            parts.pop()
            parts.append("…")
            msg = "\n".join(parts)
        if len(msg) > _TG_LIMIT:
            msg = msg[: _TG_LIMIT - 20] + "\n… (truncated)"
    return msg
