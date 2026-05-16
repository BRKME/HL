"""Render the daily HL monitor report for Telegram (HTML parse mode).

One run -> a list of message strings (chunked at ~3500 chars to stay under
Telegram's 4096 hard limit). The caller passes them to telegram_sender.send_messages.

Layout:
  Header   — date+time MSK, account value, wallet count
  Alerts   — sorted critical -> warn -> info (input is already sorted by rule engine)
  Tracked  — perp positions matched to recent decisions
  Orphan   — manual perp trades
  Spot     — coin balances (if any)
  Footer   — current OracAI regime / phase
"""
from __future__ import annotations

import html
from datetime import datetime, timezone, timedelta
from typing import Optional

from src.matcher import MatchResult
from src.monitor_rules import Alert, SEV_INFO, SEV_WARN, SEV_CRITICAL
from src.portfolio import SpotPosition


_MOSCOW = timezone(timedelta(hours=3))
_MAX_CHUNK = 3500
_RU_MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _e(s) -> str:
    return html.escape("" if s is None else str(s), quote=False)


def _fmt_price(p: Optional[float]) -> str:
    if p is None or p == 0:
        return "—"
    if p >= 1000:
        return f"{p:,.0f}".replace(",", " ")
    if p >= 10:
        return f"{p:.2f}"
    if p >= 0.01:
        return f"{p:.4f}"
    return f"{p:.8f}".rstrip("0").rstrip(".")


def _fmt_money(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ")


def _fmt_pct(p: float, sign: bool = True) -> str:
    return f"{p:+.1f}%" if sign else f"{p:.1f}%"


def _ru_date(dt: datetime) -> str:
    return f"{dt.day} {_RU_MONTHS[dt.month - 1]} {dt.year}"


_SEVERITY_MARKER = {
    SEV_CRITICAL: "🔴",
    SEV_WARN: "⚠️",
    SEV_INFO: "💰",
}

_RULE_MARKER = {
    "LIQUIDATION_CLOSE": "🔴",
    "SL_APPROACH": "🛑",
    "REGIME_FLIP_DAILY": "🌪",
    "REGIME_FLIP_SINCE_ENTRY": "🌪",
    "PHASE_FLIP_DAILY": "🌀",
    "TIME_STOP": "💤",
    "PROFIT_TRAIL": "💰",
}


def _alert_marker(a: Alert) -> str:
    return _RULE_MARKER.get(a.rule, _SEVERITY_MARKER.get(a.severity, "•"))


def _render_header(now: datetime, total_value: float, wallet_count: int) -> str:
    msk = now.astimezone(_MOSCOW)
    return (
        f"📊 <b>HL Portfolio</b> — {_ru_date(msk)}, {msk.strftime('%H:%M')} MSK\n"
        f"{wallet_count} кошельк{_plural(wallet_count, 'а', 'а', 'ов')} • "
        f"${_fmt_money(total_value)} total"
    )


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 10 < n < 20:
        return many
    n10 = n % 10
    if n10 == 1:
        return one
    if 1 < n10 < 5:
        return few
    return many


def _render_alerts(alerts: list[Alert]) -> Optional[str]:
    if not alerts:
        return None
    lines = ["", "<b>Алерты</b>"]
    for a in alerts:
        marker = _alert_marker(a)
        coin_label = "" if a.coin == "*" else f"<code>{_e(a.coin)}</code> "
        lines.append(f"{marker} {coin_label}{_e(a.message)}")
    return "\n".join(lines)


def _pnl_pct(net_size: float, entry: float, pnl: float) -> Optional[float]:
    notional = abs(net_size) * entry
    if notional <= 0:
        return None
    return pnl / notional * 100


def _daily_change_pct(mark: float, prev_day: Optional[float]) -> Optional[float]:
    if not prev_day or prev_day <= 0 or mark <= 0:
        return None
    return (mark - prev_day) / prev_day * 100


def _render_tracked(
    matches: list[MatchResult],
    marks: dict[str, float],
    prev_day_marks: Optional[dict[str, float]] = None,
) -> Optional[str]:
    tracked = [m for m in matches if m.status == "tracked"]
    if not tracked:
        return None
    prev_day_marks = prev_day_marks or {}
    lines = ["", "<b>📍 Отслеживаемые позиции</b>"]
    for m in tracked:
        pos = m.position
        dec = m.decision
        mark = marks.get(pos.coin, 0.0)
        pnl_p = _pnl_pct(pos.net_size, pos.weighted_entry, pos.total_pnl)
        side = "LONG" if pos.net_size > 0 else "SHORT"
        pnl_str = _fmt_pct(pnl_p) if pnl_p is not None else "—"
        days = m.days_in_position if m.days_in_position is not None else "?"

        daily_p = _daily_change_pct(mark, prev_day_marks.get(pos.coin))
        daily_str = f" [24h {_fmt_pct(daily_p)}]" if daily_p is not None else ""

        line = (
            f"<code>{_e(pos.coin)}</code> {side} {abs(pos.net_size):g} @ "
            f"${_fmt_price(pos.weighted_entry)} ({days}d) → "
            f"${_fmt_price(mark)} ({pnl_str}){daily_str}"
        )
        if dec and dec.sl_price > 0:
            sl_dist = abs(mark - dec.sl_price) / mark * 100 if mark > 0 else 0
            line += f" | SL ${_fmt_price(dec.sl_price)} ({_fmt_pct(sl_dist, sign=False)})"
        lines.append(line)
    return "\n".join(lines)


def _render_orphan(
    matches: list[MatchResult],
    marks: dict[str, float],
    prev_day_marks: Optional[dict[str, float]] = None,
) -> Optional[str]:
    orphans = [m for m in matches if m.status == "orphan"]
    if not orphans:
        return None
    prev_day_marks = prev_day_marks or {}
    lines = ["", "<b>🤚 Ручные / orphan позиции</b>"]
    for m in orphans:
        pos = m.position
        mark = marks.get(pos.coin, 0.0)
        pnl_p = _pnl_pct(pos.net_size, pos.weighted_entry, pos.total_pnl)
        side = "LONG" if pos.net_size > 0 else "SHORT"
        pnl_str = _fmt_pct(pnl_p) if pnl_p is not None else "—"
        liq = pos.max_liquidation_distance_pct
        liq_str = f" | до liq {_fmt_pct(liq, sign=False)}" if liq > 0 else ""

        daily_p = _daily_change_pct(mark, prev_day_marks.get(pos.coin))
        daily_str = f" [24h {_fmt_pct(daily_p)}]" if daily_p is not None else ""

        lines.append(
            f"<code>{_e(pos.coin)}</code> {side} {abs(pos.net_size):g} @ "
            f"${_fmt_price(pos.weighted_entry)} → ${_fmt_price(mark)} "
            f"({pnl_str}){daily_str}{liq_str}"
        )
    return "\n".join(lines)


def _render_spot(spot: list[SpotPosition], marks: dict[str, float]) -> Optional[str]:
    if not spot:
        return None
    lines = ["", "<b>🪙 Spot</b>"]
    for s in spot:
        mark = marks.get(s.coin, 0.0)
        avg = s.avg_entry
        if avg and mark > 0:
            pnl_p = (mark - avg) / avg * 100
            usd_value = s.total * mark
            lines.append(
                f"<code>{_e(s.coin)}</code> {s.total:g} @ ${_fmt_price(avg)} → "
                f"${_fmt_price(mark)} ({_fmt_pct(pnl_p)}) • ${_fmt_money(usd_value)}"
            )
        elif mark > 0:
            # have current mark but no cost basis (e.g. airdrop) — show USD value at least
            usd_value = s.total * mark
            lines.append(
                f"<code>{_e(s.coin)}</code> {s.total:g} @ ${_fmt_price(mark)} "
                f"• ${_fmt_money(usd_value)}"
            )
        else:
            # no mark — fallback to size only
            lines.append(f"<code>{_e(s.coin)}</code> {s.total:g}")
    return "\n".join(lines)


def _fmt_money_signed(v: float) -> str:
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):,.0f}".replace(",", " ")


def _render_performance(perf) -> Optional[str]:
    """Render the 📈 Доходность block from a PerformanceSnapshot.

    perf is duck-typed (PerformanceSnapshot from portfolio_performance) —
    we don't import it here to keep daily_report decoupled.
    """
    if perf is None:
        return None
    # everything zero → wallet hasn't traded yet; skip the block
    if (perf.day.pnl == 0 and perf.week.pnl == 0
            and perf.month.pnl == 0 and perf.all_time.pnl == 0):
        return None

    lines = ["", "<b>📈 Доходность</b>"]
    rows = [
        ("Сегодня", perf.day),
        ("Неделя ", perf.week),
        ("Месяц  ", perf.month),
        ("All-time", perf.all_time),
    ]
    for label, ps in rows:
        money = _fmt_money_signed(ps.pnl)
        roi = f"({_fmt_pct(ps.roi_pct)})" if ps.start_value > 0 else ""
        lines.append(f"  {label}: <code>{money}</code> {roi}".rstrip())
    if perf.failed_wallets:
        lines.append(f"  <i>⚠️ не удалось получить данные по "
                     f"{len(perf.failed_wallets)} кошельк{_plural(len(perf.failed_wallets), 'у', 'ам', 'ам')}</i>")
    return "\n".join(lines)


def _render_footer(snapshot: Optional[dict]) -> Optional[str]:
    if not snapshot:
        return None
    regime = snapshot.get("regime")
    phase = (snapshot.get("cycle") or {}).get("phase")
    confidence = snapshot.get("confidence")
    bits = []
    if regime:
        bits.append(f"regime <b>{_e(regime)}</b>")
    if phase:
        bits.append(f"phase <b>{_e(phase)}</b>")
    if confidence is not None:
        try:
            bits.append(f"conf {float(confidence):.0%}")
        except (TypeError, ValueError):
            pass
    if not bits:
        return None
    return "\n———\n" + " · ".join(bits)



    if not snapshot:
        return None
    regime = snapshot.get("regime")
    phase = (snapshot.get("cycle") or {}).get("phase")
    confidence = snapshot.get("confidence")
    bits = []
    if regime:
        bits.append(f"regime <b>{_e(regime)}</b>")
    if phase:
        bits.append(f"phase <b>{_e(phase)}</b>")
    if confidence is not None:
        try:
            bits.append(f"conf {float(confidence):.0%}")
        except (TypeError, ValueError):
            pass
    if not bits:
        return None
    return "\n———\n" + " · ".join(bits)


def _chunk(text: str, max_size: int = _MAX_CHUNK) -> list[str]:
    """Split a long message at line boundaries, keep each chunk under max_size."""
    if len(text) <= max_size:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.split("\n"):
        line_len = len(line) + 1
        if current_len + line_len > max_size and current:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def render_daily_report(
    matches: list[MatchResult],
    alerts: list[Alert],
    marks: dict[str, float],
    current_snapshot: Optional[dict],
    total_account_value: float,
    now: datetime,
    spot: Optional[list[SpotPosition]] = None,
    wallet_count: int = 3,
    prev_day_marks: Optional[dict[str, float]] = None,
    performance=None,
) -> list[str]:
    """Build the Telegram report. Returns a list of message-sized chunks."""
    parts: list[str] = [_render_header(now, total_account_value, wallet_count)]

    perf_block = _render_performance(performance)
    if perf_block:
        parts.append(perf_block)

    alerts_block = _render_alerts(alerts)
    if alerts_block:
        parts.append(alerts_block)
    elif matches or (spot and any(spot)):
        parts.append("\n✅ Без алертов, всё спокойно")

    tracked_block = _render_tracked(matches, marks, prev_day_marks)
    if tracked_block:
        parts.append(tracked_block)

    orphan_block = _render_orphan(matches, marks, prev_day_marks)
    if orphan_block:
        parts.append(orphan_block)

    spot_block = _render_spot(spot or [], marks)
    if spot_block:
        parts.append(spot_block)

    if not matches and not (spot and any(spot)):
        parts.append("\nПозиций нет — портфель пуст.")

    footer = _render_footer(current_snapshot)
    if footer:
        parts.append(footer)

    full = "\n".join(parts)
    return _chunk(full)
