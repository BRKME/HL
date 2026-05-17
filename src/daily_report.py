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
    """Format a price for display.

    Round prices to integers for >= 1 (UX feedback: visual noise from
    decimals on $78101.55, $2186.40 isn't useful for decision-making).
    Sub-1 prices (memecoins) keep precision because their decimals
    *are* the price.
    """
    if p is None or p == 0:
        return "—"
    if p >= 1000:
        return f"{round(p):,}".replace(",", " ")
    if p >= 1:
        return f"{round(p)}"
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


def _render_header(
    now: datetime,
    total_value: float,
    wallet_count: int,
    matches: Optional[list[MatchResult]] = None,
    marks: Optional[dict[str, float]] = None,
    performance=None,
) -> str:
    """Compact executive summary header.

    Lines:
      📊 HL Portfolio — 16 мая 2026, 16:04 MSK
      $1 573 • Day -$64 (-2.6%) • Exposure 2.0× • Top: ETH 100%

    'Day' is here in the header (replaces the 'Сегодня' row in the perf
    block) so the most actionable PnL is at the top. 'Exposure' = sum of
    abs notional / total account value — shows real leverage from cross-
    margin. 'Top:' = largest position by USD value with concentration %.
    """
    msk = now.astimezone(_MOSCOW)
    matches = matches or []
    marks = marks or {}

    parts: list[str] = []
    parts.append(f"${_fmt_money(total_value)}")

    # Day PnL — from performance.day if available
    if performance is not None and performance.day.start_value > 0:
        money = _fmt_money_signed(performance.day.pnl)
        roi = _fmt_pct(performance.day.roi_pct)
        parts.append(f"Day <code>{money}</code> ({roi})")

    # Exposure + top position concentration
    if total_value > 0 and matches:
        exposures = _compute_exposures(matches, marks)
        if exposures:
            total_exposure = sum(e["value"] for e in exposures)
            leverage = total_exposure / total_value
            # Leverage badge: leveraged >= 1.5× is system-level risk —
            # call it out with ⚠️ so it doesn't get lost in the line.
            badge = "⚠️ " if leverage >= 1.5 else ""
            parts.append(f"{badge}Exposure {leverage:.1f}×")
            top = max(exposures, key=lambda e: e["value"])
            top_pct = top["value"] / total_value * 100
            parts.append(f"Top: {_e(top['coin'])} {top_pct:.0f}%")

    summary = " • ".join(parts)

    return (
        f"📊 <b>HL Portfolio</b> — {_ru_date(msk)}, {msk.strftime('%H:%M')} MSK\n"
        f"{wallet_count} кошельк{_plural(wallet_count, 'а', 'а', 'ов')} • {summary}"
    )


def _compute_exposures(
    matches: list[MatchResult],
    marks: dict[str, float],
) -> list[dict]:
    """For each position: USD value = |net_size| × mark (fallback to entry).

    Used by header (leverage + top concentration) and by orphan renderer
    (per-position '$X (Y%)').
    """
    out: list[dict] = []
    for m in matches:
        pos = m.position
        mark = marks.get(pos.coin) or pos.weighted_entry
        value = abs(pos.net_size) * mark
        if value <= 0:
            continue
        out.append({"coin": pos.coin, "value": value, "match": m})
    return out


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
    sl_orders: Optional[list] = None,
    total_account_value: float = 0.0,
    coin_atrs: Optional[dict[str, float]] = None,
) -> Optional[str]:
    """Two-line per orphan:

      ETH LONG 0.7238 @ $2173 → $2175 (+0.0%) [24h -3.5%]  $1 575 (100%)
        SL $2122 (2.4% = $43 if hit • 1.5× ATR) ⚠️ • liq buffer 88%

    Line 1 = position + USD value + concentration %
    Line 2 = risk (SL + ATR-relative distance + liq buffer).

    Positions sorted by SL distance ascending (tightest stop = most
    urgent = on top). Positions without SL go to the bottom.
    """
    orphans = [m for m in matches if m.status == "orphan"]
    if not orphans:
        return None
    prev_day_marks = prev_day_marks or {}
    sl_orders = sl_orders or []
    coin_atrs = coin_atrs or {}
    from src.sl_visibility import find_sl_for_position

    # Sort by SL distance ascending; no-SL positions go to end.
    def _sl_dist_for_sort(m: MatchResult) -> float:
        pos = m.position
        mark = marks.get(pos.coin, 0.0)
        sl = find_sl_for_position(pos, sl_orders)
        if sl is None or mark <= 0:
            return 1e9  # sentinel: bottom of list
        return abs(mark - sl.trigger_px) / mark * 100

    orphans_sorted = sorted(orphans, key=_sl_dist_for_sort)

    lines = ["", "<b>🤚 Ручные / orphan позиции</b>"]
    for m in orphans_sorted:
        pos = m.position
        mark = marks.get(pos.coin, 0.0)
        pnl_p = _pnl_pct(pos.net_size, pos.weighted_entry, pos.total_pnl)
        side = "LONG" if pos.net_size > 0 else "SHORT"
        pnl_str = _fmt_pct(pnl_p) if pnl_p is not None else "—"

        # USD value of position + concentration %
        eff_mark = mark or pos.weighted_entry
        usd_value = abs(pos.net_size) * eff_mark
        if total_account_value > 0:
            concentration = usd_value / total_account_value * 100
            value_str = f"  ${_fmt_money(usd_value)} ({concentration:.0f}%)"
        else:
            value_str = f"  ${_fmt_money(usd_value)}"

        daily_p = _daily_change_pct(mark, prev_day_marks.get(pos.coin))
        daily_str = f" [24h {_fmt_pct(daily_p)}]" if daily_p is not None else ""

        # Line 1: position
        lines.append(
            f"<code>{_e(pos.coin)}</code> {side} {abs(pos.net_size):g} @ "
            f"${_fmt_price(pos.weighted_entry)} → ${_fmt_price(mark)} "
            f"({pnl_str}){daily_str}{value_str}"
        )

        # Line 2: risk
        risk_bits: list[str] = []
        sl = find_sl_for_position(pos, sl_orders)
        if sl is not None and mark > 0:
            sl_dist_abs = abs(mark - sl.trigger_px)
            sl_dist_pct = sl_dist_abs / mark * 100
            # warn marker when within 3% of SL
            sl_warn = " ⚠️" if sl_dist_pct <= 3.0 else ""
            # max loss in USD if SL hits: |mark - sl| × size
            max_loss = sl_dist_abs * abs(pos.net_size)
            loss_str = f" = ${_fmt_money(max_loss)} if hit"

            # ATR-relative distance — gives volatility-units context
            atr_str = ""
            atr_val = coin_atrs.get(pos.coin)
            if atr_val and atr_val > 0:
                atr_mult = sl_dist_abs / atr_val
                # Categorise: <0.5 ATR = likely intraday, <1 ATR = within a day,
                # <2 ATR = tight stop, otherwise just the number
                if atr_mult < 0.5:
                    qual = " — likely intraday"
                elif atr_mult < 1.0:
                    qual = " — within a day"
                else:
                    qual = ""
                atr_str = f" • {atr_mult:.1f}× ATR{qual}"

            risk_bits.append(
                f"SL ${_fmt_price(sl.trigger_px)} "
                f"({_fmt_pct(sl_dist_pct, sign=False)}{loss_str}{atr_str}){sl_warn}"
            )
        else:
            risk_bits.append("⚠️ нет SL")

        liq = pos.max_liquidation_distance_pct
        if liq > 0:
            risk_bits.append(f"liq buffer {_fmt_pct(liq, sign=False)}")

        lines.append("  " + " • ".join(risk_bits))
    return "\n".join(lines)


def _render_spot(spot: list[SpotPosition], marks: dict[str, float],
                 display_dust_usd: float = 5.0) -> Optional[str]:
    """Render spot block. Filters out positions whose current USD value < threshold.

    This is a *display* dust filter (separate from Portfolio.from_raw's
    entry_notional filter): catches airdrop holdings with cost-basis 0 that
    later dust out (e.g. OMNIX dropping to $1 of value).
    """
    if not spot:
        return None
    lines = ["", "<b>🪙 Spot</b>"]
    rendered = 0
    for s in spot:
        mark = marks.get(s.coin, 0.0)
        # USD value gate: if we have a mark, drop anything under threshold
        if mark > 0 and s.total * mark < display_dust_usd:
            continue
        avg = s.avg_entry
        if avg and mark > 0:
            pnl_p = (mark - avg) / avg * 100
            usd_value = s.total * mark
            lines.append(
                f"<code>{_e(s.coin)}</code> {s.total:g} @ ${_fmt_price(avg)} → "
                f"${_fmt_price(mark)} ({_fmt_pct(pnl_p)}) • ${_fmt_money(usd_value)}"
            )
        elif mark > 0:
            usd_value = s.total * mark
            lines.append(
                f"<code>{_e(s.coin)}</code> {s.total:g} @ ${_fmt_price(mark)} "
                f"• ${_fmt_money(usd_value)}"
            )
        else:
            lines.append(f"<code>{_e(s.coin)}</code> {s.total:g}")
        rendered += 1
    if rendered == 0:
        return None
    return "\n".join(lines)


def _fmt_money_signed(v: float) -> str:
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):,.0f}".replace(",", " ")


def _render_performance(perf, day_already_shown: bool = False) -> Optional[str]:
    """Render the 📈 Доходность block from a PerformanceSnapshot.

    When day_already_shown=True (the common case — header shows it),
    'Сегодня' is omitted to avoid duplication.
    """
    if perf is None:
        return None
    if (perf.day.pnl == 0 and perf.week.pnl == 0
            and perf.month.pnl == 0 and perf.all_time.pnl == 0):
        return None

    lines = ["", "<b>📈 Доходность</b>"]
    rows: list[tuple[str, object]] = []
    if not day_already_shown:
        rows.append(("Сегодня ", perf.day))
    rows.append(("Неделя  ", perf.week))
    rows.append(("Месяц   ", perf.month))
    # All-time removed (UX feedback round 2): -55% anchor bias hurts
    # current-EV decision frame for active trading.

    for label, ps in rows:
        money = _fmt_money_signed(ps.pnl)
        # primary: HL-provided ROI from start_value
        if ps.start_value > 0:
            roi = f"({_fmt_pct(ps.roi_pct)})"
        else:
            # fallback: derive ROI from pnl and end_value when HL didn't
            # return start_value (happens for all-time on accounts with
            # incomplete history). Implied start = end_value - pnl.
            implied_start = ps.end_value - ps.pnl
            if implied_start > 0:
                roi_calc = ps.pnl / implied_start * 100
                roi = f"({_fmt_pct(roi_calc)})"
            else:
                roi = ""
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
    sl_orders: Optional[list] = None,
    coin_atrs: Optional[dict[str, float]] = None,
) -> list[str]:
    """Build the Telegram report. Returns a list of message-sized chunks."""
    parts: list[str] = [_render_header(
        now, total_account_value, wallet_count,
        matches=matches, marks=marks, performance=performance,
    )]

    # Action required first (UX feedback round 2): alerts before PnL
    alerts_block = _render_alerts(alerts)
    if alerts_block:
        parts.append(alerts_block)
    elif matches or (spot and any(spot)):
        parts.append("\n✅ Без алертов, всё спокойно")

    # If header surfaced day PnL, don't repeat it in the perf block
    day_in_header = performance is not None and performance.day.start_value > 0
    perf_block = _render_performance(performance, day_already_shown=day_in_header)
    if perf_block:
        parts.append(perf_block)

    tracked_block = _render_tracked(matches, marks, prev_day_marks)
    if tracked_block:
        parts.append(tracked_block)

    orphan_block = _render_orphan(
        matches, marks, prev_day_marks,
        sl_orders=sl_orders,
        total_account_value=total_account_value,
        coin_atrs=coin_atrs,
    )
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
