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
    """Single-line executive header (UI simplification round 3).

    Format:
      📊 HL Portfolio — 17 мая 2026, 16:46 MSK • $2 347

    Removed (UX feedback round 3):
    - wallet count ('3 кошелька') — not informational
    - Day PnL — moved into Доходность block
    - Exposure leverage badge — user always runs at 2×
    - Top concentration — replaced by full Веса line below
    """
    msk = now.astimezone(_MOSCOW)
    return (
        f"📊 <b>HL Portfolio</b> — {_ru_date(msk)}, {msk.strftime('%H:%M')} MSK "
        f"• ${_fmt_money(total_value)}"
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


def _render_weights(
    matches: list[MatchResult],
    marks: dict[str, float],
) -> Optional[str]:
    """Portfolio weights line, normalised to 100% across all positions.

    Format: 'Веса: ETH 34% • TAO 22% • HYPE 19% • BTC 13% • ZEC 9% • SOPH 3%'

    Each weight = pos_notional / total_notional (NOT / account_value).
    This sums to 100% regardless of leverage — the bar always normalised
    to 'how is my portfolio distributed' rather than 'what's my leverage'.
    Leverage info is removed elsewhere (UX feedback round 3: user always
    operates at 2×, badge wasn't informative).
    """
    exposures = _compute_exposures(matches, marks)
    if not exposures:
        return None
    total = sum(e["value"] for e in exposures)
    if total <= 0:
        return None
    exposures.sort(key=lambda e: -e["value"])  # largest first
    bits = []
    for e in exposures:
        pct = e["value"] / total * 100
        bits.append(f"{_e(e['coin'])} {pct:.0f}%")
    return "Веса: " + " • ".join(bits)


def _render_wallets(wallet_values: Optional[dict[str, float]]) -> Optional[str]:
    """Per-wallet balance line: 'Кошельки: 1 $1 050 • Marta $80 • Arkadii $30'.

    Preserves insertion order of wallet_values (= whitelist.yaml order).
    Wallets with $0 balance are still shown — explicit '$0' is informative
    (you know that wallet exists and is empty, not 'forgot').
    """
    if not wallet_values:
        return None
    bits = []
    for label, val in wallet_values.items():
        bits.append(f"{_e(str(label))} ${_fmt_money(val)}")
    return "Кошельки: " + " • ".join(bits)


# Regime advice table — short imperative for each regime × phase combo.
# Surfaces what to do/not-do given the broad market environment, so the
# user sees actionable guidance up top instead of just decorative
# 'regime BEAR · phase EARLY_BEAR' at the bottom.
#
# Rules:
# - BEAR/EARLY_BEAR or LATE_BULL: warning to cut risk / lock profits
# - BEAR/MID_BEAR: deep bear, cash is position
# - BEAR/LATE_BEAR or EARLY_BULL: bottom-fishing zone
# - BULL/MID_BULL: trend trading is the play
# - TRANSITION: don't add risk, wait for clarity
# - Unknown: skip advice block entirely
_REGIME_ADVICE: dict[tuple[str, str], str] = {
    ("BEAR", "EARLY_BEAR"):
        "🛑 Снижай риск: цикл переходит в медвежий. Закрывай слабые позиции, "
        "не открывай новые лонги без сильного сигнала.",
    ("BEAR", "MID_BEAR"):
        "💵 Глубокий медвежий: кэш — позиция. Лонги только на чёткое "
        "перепроданность с подтверждением, размер маленький.",
    ("BEAR", "LATE_BEAR"):
        "🎯 Конец медвежьего цикла: начало накопления. Аккуратно набирай "
        "долгосрочные позиции в качественных активах.",
    # Wyckoff/cycle terminology — OracAI may emit these instead of phase names
    ("BEAR", "CAPITULATION"):
        "🔥 Капитуляция: панические продажи на дне. Исторически — лучшая "
        "точка входа в long на качественные активы. Размер маленький, SL "
        "под локальный минимум.",
    ("BEAR", "ACCUMULATION"):
        "🎯 Аккумуляция: рынок собирает позиции на дне. Набирай качество "
        "лесенкой, готовься к развороту.",
    ("BEAR", "DISTRIBUTION"):
        "⚠️ Distribution в медвежьем — крупные игроки сбрасывают. "
        "Не входи в long, рассмотри short от уровней.",
    ("BULL", "EARLY_BULL"):
        "🚀 Начало бычьего цикла: добавляй риск, держи победителей, "
        "обрезай убытки быстро.",
    ("BULL", "MID_BULL"):
        "📈 Бычий цикл в разгаре: торгуй тренд, не угадывай разворот. "
        "Держи стопы дальше, чем хочется.",
    ("BULL", "MARKUP"):
        "📈 Markup phase: тренд устойчив, торгуй pullback в long. "
        "Не пытайся ловить вершину.",
    ("BULL", "LATE_BULL"):
        "⚠️ Поздняя стадия бычки: фиксируй прибыль, подтягивай стопы. "
        "Новые входы только с близким SL.",
    ("BULL", "ACCUMULATION"):
        "🎯 Аккумуляция в бычьем: ранняя стадия восходящего тренда. "
        "Набирай позиции, время на твоей стороне.",
    ("BULL", "DISTRIBUTION"):
        "⚠️ Distribution на верхах бычьего — крупные продают розничным. "
        "Фиксируй прибыль, новые лонги — с близким SL.",
    ("BULL", "EUPHORIA"):
        "🚨 Эйфория: рынок overbought, retail в плюсе, все говорят 'to the moon'. "
        "Time to leave the party — фиксируй прибыль, шорты от уровней.",
    ("TRANSITION", "EARLY_BEAR"):
        "⏸ Переход к bear: не наращивай позиции, дай рынку показать "
        "направление прежде чем действовать.",
    ("TRANSITION", "EARLY_BULL"):
        "👀 Возможен переход к bull: следи за подтверждением, но пока "
        "позиции не наращивай.",
    ("TRANSITION", "CAPITULATION"):
        "🔥 Капитуляция в переходе: возможное дно рынка. Аккуратные long "
        "малым размером на качественные активы.",
}


def _render_regime_advice(snapshot: Optional[dict]) -> Optional[str]:
    """One-line actionable advice based on regime × phase from OracAI.

    Shown after wallets line, before alerts. The footer still prints
    'regime X · phase Y' as the raw label.
    """
    if not snapshot:
        return None
    regime = snapshot.get("regime")
    phase = (snapshot.get("cycle") or {}).get("phase")
    if not regime or not phase:
        return None
    advice = _REGIME_ADVICE.get((regime, phase))
    if not advice:
        return None
    return advice


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
    """Render alerts block.

    Round 3: SL_APPROACH-family messages are self-contained ('🔴 BTC: SL
    вышибет внутри дня'). Other rules still emit terse text — we prefix
    those with a severity marker (⚠️/🔴) and the coin code.
    """
    if not alerts:
        return None
    lines = ["", "<b>Алерты</b>"]
    for a in alerts:
        msg = a.message
        # Self-contained messages already start with their own marker emoji
        if msg.lstrip().startswith(("🔴", "⚠️", "✅", "🟠")):
            lines.append(_e(msg))
        else:
            marker = _alert_marker(a)
            coin_label = "" if a.coin == "*" else f"<code>{_e(a.coin)}</code> "
            lines.append(f"{marker} {coin_label}{_e(msg)}")
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
    """One-line per orphan (UI simplification round 3):

      BTC LONG • $609 • [24h +0.5%] • SL: -$7
      🔴 ETH LONG • $1 587 • [24h +0.7%]    ← no SL

    Format: COIN SIDE • $value • [24h ±%] • SL: -$max_loss
    Positions without SL get 🔴 prefix and no SL field.

    Removed (per UX feedback round 3):
    - entry @ price, mark, current PnL%, concentration %
    - ATR units in row (still used for alert threshold)
    - SL price, SL distance %, liq buffer

    Positions sorted by SL distance ascending (tightest stop on top).
    Positions without SL go to the bottom — they need addressing, but
    the red marker is the call to action, not sort order.
    """
    orphans = [m for m in matches if m.status == "orphan"]
    if not orphans:
        return None
    prev_day_marks = prev_day_marks or {}
    sl_orders = sl_orders or []
    from src.sl_visibility import find_sl_for_position

    # Sort alphabetically by coin name (round 3 follow-up):
    # SL-distance sort was nice in principle, but with the simplified row
    # format alphabetical is easier to scan for a specific position.
    orphans_sorted = sorted(orphans, key=lambda m: m.position.coin)

    lines = ["", "<b>Ручные позиции</b>"]
    for m in orphans_sorted:
        pos = m.position
        mark = marks.get(pos.coin, 0.0)
        side = "LONG" if pos.net_size > 0 else "SHORT"

        eff_mark = mark or pos.weighted_entry
        usd_value = abs(pos.net_size) * eff_mark
        value_str = f"${_fmt_money(usd_value)}"

        # Unrealized PnL on this open position (round 3 follow-up):
        # replaces [24h ±%]. Comes straight from HL clearinghouseState as
        # total_pnl, summed across the contributing wallets. Reflects
        # mark-to-market PnL from entry — funding included by HL.
        pnl_str = f"[{_fmt_money_signed(pos.total_pnl)}]"

        sl = find_sl_for_position(pos, sl_orders)
        if sl is not None and mark > 0:
            max_loss = abs(mark - sl.trigger_px) * abs(pos.net_size)
            sl_str = f"SL: -${_fmt_money(max_loss)}"
            prefix = ""
        else:
            sl_str = ""
            prefix = "🔴 "

        bits = [f"<code>{_e(pos.coin)}</code> {side}", value_str, pnl_str]
        if sl_str:
            bits.append(sl_str)

        lines.append(f"{prefix}" + " • ".join(bits))
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

    UI simplification round 3: Day now always shown here (header doesn't
    duplicate it). day_already_shown param kept for backward compat with
    older callers but ignored — always renders Day.
    """
    if perf is None:
        return None
    if (perf.day.pnl == 0 and perf.week.pnl == 0
            and perf.month.pnl == 0 and perf.all_time.pnl == 0):
        return None

    lines = ["", "<b>📈 Доходность</b>"]
    rows: list[tuple[str, object]] = [
        ("Day    ", perf.day),
        ("Неделя ", perf.week),
        ("Месяц  ", perf.month),
    ]
    # All-time removed (UX feedback round 2): -55% anchor bias hurts
    # current-EV decision frame for active trading.

    for label, ps in rows:
        money = _fmt_money_signed(ps.pnl)
        # primary: HL-provided ROI from start_value
        if ps.start_value > 0:
            roi = f"({_fmt_pct(ps.roi_pct)})"
        else:
            # fallback: derive ROI from pnl and end_value when HL didn't
            # return start_value. Implied start = end_value - pnl.
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
    wallet_values: Optional[dict[str, float]] = None,
) -> list[str]:
    """Build the Telegram report. Returns a list of message-sized chunks."""
    parts: list[str] = [_render_header(
        now, total_account_value, wallet_count,
        matches=matches, marks=marks, performance=performance,
    )]

    # Веса block: portfolio weights normalised to 100%
    weights_block = _render_weights(matches, marks)
    if weights_block:
        parts.append(weights_block)

    # Кошельки line: balance per wallet, in whitelist order
    wallets_block = _render_wallets(wallet_values)
    if wallets_block:
        parts.append(wallets_block)

    # Regime advice: short imperative based on broad market phase
    advice_block = _render_regime_advice(current_snapshot)
    if advice_block:
        parts.append("\n" + advice_block)

    # Action required first (UX feedback round 2): alerts before PnL
    alerts_block = _render_alerts(alerts)
    if alerts_block:
        parts.append(alerts_block)
    elif matches or (spot and any(spot)):
        parts.append("\n✅ Без алертов, всё спокойно")

    # UI round 3: Day now lives in perf block, not header
    perf_block = _render_performance(performance)
    if perf_block:
        parts.append(perf_block)

    tracked_block = _render_tracked(matches, marks, prev_day_marks)
    if tracked_block:
        parts.append(tracked_block)

    orphan_block = _render_orphan(
        matches, marks, prev_day_marks,
        sl_orders=sl_orders,
        total_account_value=total_account_value,
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
