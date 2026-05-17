"""Rule engine for daily HL monitor.

Each rule is a pure function that takes (MatchResult, context) and returns a
list of Alert objects (zero, one, or many). Alerts have a severity so the
Telegram report can sort and emoji them.

Rules (Phase 1.3):
- SL_APPROACH:            mark close to recommended SL (tracked + orphan)
- TIME_STOP:              days_in_position >= N (tracked only)
- REGIME_FLIP_SINCE_ENTRY: current regime != regime_at_entry (tracked only)
- REGIME_FLIP_DAILY:      yesterday's regime != today's regime (portfolio-wide,
                          emitted once if so, not per-position)
- PROFIT_TRAIL:           unrealized PnL >= +Y% (tracked + orphan)
- LIQUIDATION_CLOSE:      distance to liquidation < threshold (tracked + orphan)

FUNDING_DRAIN and EMA20_BREAKDOWN deferred to Phase 1.5.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.matcher import MatchResult


# Severity ordering (higher = louder; used for sorting Telegram output)
SEV_INFO = 1
SEV_WARN = 2
SEV_CRITICAL = 3


@dataclass(frozen=True)
class Alert:
    rule: str             # rule identifier, e.g. "SL_APPROACH"
    severity: int         # SEV_INFO | SEV_WARN | SEV_CRITICAL
    coin: str             # affected coin, or "*" for portfolio-wide
    message: str          # human-readable description
    details: dict         # structured fields for the report template


def _fmt_alert_price(p: float) -> str:
    """Format a price for inclusion in an alert message.

    Mirrors daily_report._fmt_price so alerts and orphan rows show the
    same numbers (integers for >=1, decimals only for sub-1 prices like
    memecoins).
    """
    if p is None or p == 0:
        return "0"
    if p >= 1000:
        return f"{round(p):,}".replace(",", " ")
    if p >= 1:
        return f"{round(p)}"
    if p >= 0.01:
        return f"{p:.4f}"
    return f"{p:.8f}".rstrip("0").rstrip(".")


# ---------- per-position rules ----------

def rule_sl_approach(
    match: MatchResult,
    current_mark: float,
    warning_pct: float = 3.0,
) -> list[Alert]:
    """Alert if mark price is within `warning_pct` of the recommended SL.

    Only applies if we have a recommended SL (tracked positions). For orphans
    we don't know the user's SL — skip.
    """
    if not match.decision or match.decision.sl_price <= 0:
        return []
    sl = match.decision.sl_price
    pos = match.position
    if pos.side == "long":
        # SL below mark; distance = (mark - sl) / mark
        if current_mark <= 0:
            return []
        distance_pct = (current_mark - sl) / current_mark * 100
    else:
        # short: SL above mark
        if current_mark <= 0:
            return []
        distance_pct = (sl - current_mark) / current_mark * 100

    if distance_pct < 0:
        # already past SL — critical
        return [Alert(
            rule="SL_APPROACH",
            severity=SEV_CRITICAL,
            coin=pos.coin,
            message=f"{pos.coin}: mark ${_fmt_alert_price(current_mark)} BEYOND SL ${_fmt_alert_price(sl)}",
            details={"sl_price": sl, "mark": current_mark, "distance_pct": distance_pct},
        )]
    if distance_pct <= warning_pct:
        return [Alert(
            rule="SL_APPROACH",
            severity=SEV_WARN,
            coin=pos.coin,
            message=f"{pos.coin}: mark ${_fmt_alert_price(current_mark)} within {distance_pct:.1f}% of SL ${_fmt_alert_price(sl)}",
            details={"sl_price": sl, "mark": current_mark, "distance_pct": distance_pct},
        )]
    return []


def rule_time_stop(match: MatchResult, max_days: int = 7) -> list[Alert]:
    """Alert if a tracked position has been open >= max_days."""
    if match.status != "tracked" or match.days_in_position is None:
        return []
    if match.days_in_position < max_days:
        return []
    pos = match.position
    return [Alert(
        rule="TIME_STOP",
        severity=SEV_WARN,
        coin=pos.coin,
        message=f"{pos.coin}: in position {match.days_in_position} days (limit {max_days})",
        details={"days": match.days_in_position, "limit": max_days},
    )]


def rule_regime_flip_since_entry(
    match: MatchResult,
    current_regime: Optional[str],
) -> list[Alert]:
    """Alert if current regime differs from regime when this trade was entered."""
    if match.status != "tracked" or not match.decision:
        return []
    entry_regime = match.decision.regime_at_entry
    if not entry_regime or not current_regime:
        return []
    if entry_regime == current_regime:
        return []
    pos = match.position
    return [Alert(
        rule="REGIME_FLIP_SINCE_ENTRY",
        severity=SEV_WARN,
        coin=pos.coin,
        message=f"{pos.coin}: regime changed since entry: {entry_regime} → {current_regime}",
        details={"entry_regime": entry_regime, "current_regime": current_regime},
    )]


def rule_profit_trail(match: MatchResult, threshold_pct: float = 10.0) -> list[Alert]:
    """Alert if a position is up >= threshold_pct — suggest tightening SL."""
    pos = match.position
    if pos.weighted_entry <= 0 or pos.net_size == 0:
        return []
    # PnL percent on notional at entry — use total_pnl divided by notional at entry
    notional_at_entry = abs(pos.net_size) * pos.weighted_entry
    if notional_at_entry <= 0:
        return []
    pnl_pct = pos.total_pnl / notional_at_entry * 100
    if pnl_pct < threshold_pct:
        return []
    return [Alert(
        rule="PROFIT_TRAIL",
        severity=SEV_INFO,
        coin=pos.coin,
        message=f"{pos.coin}: up {pnl_pct:+.1f}% — consider trailing SL",
        details={"pnl_pct": pnl_pct, "threshold": threshold_pct},
    )]


def rule_liquidation_close(match: MatchResult, critical_pct: float = 15.0) -> list[Alert]:
    """Alert if distance to liquidation < critical_pct.

    This is the only critical-severity rule from on-position data — liquidation
    is a hard loss, unlike all other rules which are advisory.
    """
    pos = match.position
    if pos.max_liquidation_distance_pct <= 0:
        # spot-like or stale; skip
        return []
    if pos.max_liquidation_distance_pct >= critical_pct:
        return []
    return [Alert(
        rule="LIQUIDATION_CLOSE",
        severity=SEV_CRITICAL,
        coin=pos.coin,
        message=f"{pos.coin}: only {pos.max_liquidation_distance_pct:.1f}% from liquidation",
        details={"distance_pct": pos.max_liquidation_distance_pct},
    )]


def rule_orphan_sl_approach(
    match: MatchResult,
    current_mark: float,
    sl_order,  # SLOrder | None
    coin_atr: Optional[float] = None,
    atr_threshold: float = 0.5,
    pct_fallback: float = 1.5,
    warning_pct: float = 3.0,  # legacy kw, kept for backward compat
) -> list[Alert]:
    """Alert ONLY when an orphan SL is dangerously tight (UX round 3).

    Philosophy: if SL is set on HL, its normal triggering is the plan —
    no alert needed. Alert only when SL is so close that it will almost
    certainly trigger today, so the user can decide whether to act before
    the exchange decides for them.

    Tightness threshold:
    - If ATR is known: distance < 0.5× ATR ('likely intraday touch')
    - If ATR unknown: distance < 1.5% percent (rough volatility proxy)
    - Past SL: still critical

    Message format is intentionally short:
      'BTC: SL вышибет внутри дня (0.4× ATR)'
      'ETH: SL за SL (mark $2180 < $2200)'
    """
    if match.status != "orphan" or sl_order is None:
        return []
    if current_mark <= 0 or sl_order.trigger_px <= 0:
        return []
    pos = match.position
    if pos.side == "long":
        distance_abs = current_mark - sl_order.trigger_px
    else:
        distance_abs = sl_order.trigger_px - current_mark
    distance_pct = distance_abs / current_mark * 100

    if distance_pct < 0:
        # already past SL — critical
        return [Alert(
            rule="ORPHAN_SL_APPROACH",
            severity=SEV_CRITICAL,
            coin=pos.coin,
            message=f"🔴 {pos.coin}: mark прошёл за SL (${_fmt_alert_price(current_mark)} vs ${_fmt_alert_price(sl_order.trigger_px)})",
            details={"sl_price": sl_order.trigger_px, "mark": current_mark,
                     "distance_pct": distance_pct},
        )]

    # Tightness check
    if coin_atr is not None and coin_atr > 0:
        atr_mult = distance_abs / coin_atr
        if atr_mult < atr_threshold:
            return [Alert(
                rule="ORPHAN_SL_APPROACH",
                severity=SEV_WARN,
                coin=pos.coin,
                message=f"🔴 {pos.coin}: SL вышибет внутри дня ({atr_mult:.1f}× ATR)",
                details={"sl_price": sl_order.trigger_px, "mark": current_mark,
                         "distance_pct": distance_pct, "atr_mult": atr_mult},
            )]
        return []

    # No ATR — fall back to percent threshold for tight SL
    if distance_pct <= pct_fallback:
        return [Alert(
            rule="ORPHAN_SL_APPROACH",
            severity=SEV_WARN,
            coin=pos.coin,
            message=f"🔴 {pos.coin}: SL очень близко ({distance_pct:.1f}%)",
            details={"sl_price": sl_order.trigger_px, "mark": current_mark,
                     "distance_pct": distance_pct},
        )]
    return []


def rule_no_sl_order(match: MatchResult, sl_order) -> list[Alert]:
    """Warn if an orphan position has no SL on HL — user is unprotected.

    Tracked positions go through rule_sl_approach with rec_sl, so they're
    excluded. For orphans, no SL = no bottom on loss.
    """
    if match.status != "orphan" or sl_order is not None:
        return []
    pos = match.position
    notional = abs(pos.net_size) * pos.weighted_entry
    return [Alert(
        rule="NO_SL_ORDER",
        severity=SEV_WARN,
        coin=pos.coin,
        message=f"🔴 {pos.coin}: нет SL на бирже",
        details={"coin": pos.coin, "side": pos.side, "notional_usd": notional},
    )]


# ---------- portfolio-wide rule ----------

def rule_regime_flip_daily(
    yesterday_snapshot: Optional[dict],
    today_snapshot: Optional[dict],
) -> list[Alert]:
    """Single alert if regime or phase flipped between yesterday and today.

    Independent of decisions or positions — surfaced once per run, applies
    to everything in the portfolio (including manually-opened orphans).
    """
    from src.oracai_history import regime_changed, phase_changed
    alerts: list[Alert] = []
    rc = regime_changed(yesterday_snapshot, today_snapshot)
    if rc:
        prev, curr = rc
        alerts.append(Alert(
            rule="REGIME_FLIP_DAILY",
            severity=SEV_WARN,
            coin="*",
            message=f"Regime flipped overnight: {prev} → {curr}",
            details={"prev_regime": prev, "current_regime": curr},
        ))
    pc = phase_changed(yesterday_snapshot, today_snapshot)
    if pc:
        prev, curr = pc
        alerts.append(Alert(
            rule="PHASE_FLIP_DAILY",
            severity=SEV_INFO,
            coin="*",
            message=f"Cycle phase changed overnight: {prev} → {curr}",
            details={"prev_phase": prev, "current_phase": curr},
        ))
    return alerts


# ---------- coordinator ----------

@dataclass
class RuleConfig:
    sl_warning_pct: float = 3.0
    time_stop_days: int = 7
    profit_trail_pct: float = 10.0
    liquidation_critical_pct: float = 15.0


def evaluate_all(
    matches: list[MatchResult],
    marks: dict[str, float],
    current_snapshot: Optional[dict],
    yesterday_snapshot: Optional[dict],
    config: Optional[RuleConfig] = None,
    sl_orders: Optional[list] = None,
    coin_atrs: Optional[dict[str, float]] = None,
) -> list[Alert]:
    """Run every rule against every position and aggregate alerts."""
    config = config or RuleConfig()
    sl_orders = sl_orders or []
    coin_atrs = coin_atrs or {}
    current_regime = (current_snapshot or {}).get("regime")
    out: list[Alert] = []

    # portfolio-wide first (so it appears at top after sort)
    out.extend(rule_regime_flip_daily(yesterday_snapshot, current_snapshot))

    # late-import to avoid cycle
    from src.sl_visibility import find_sl_for_position

    for m in matches:
        coin = m.position.coin
        mark = marks.get(coin, 0.0)
        sl_for_pos = find_sl_for_position(m.position, sl_orders)
        atr_for_pos = coin_atrs.get(coin)
        out.extend(rule_liquidation_close(m, critical_pct=config.liquidation_critical_pct))
        out.extend(rule_sl_approach(m, current_mark=mark, warning_pct=config.sl_warning_pct))
        out.extend(rule_orphan_sl_approach(
            m, current_mark=mark, sl_order=sl_for_pos,
            coin_atr=atr_for_pos,
        ))
        out.extend(rule_no_sl_order(m, sl_order=sl_for_pos))
        out.extend(rule_time_stop(m, max_days=config.time_stop_days))
        out.extend(rule_regime_flip_since_entry(m, current_regime=current_regime))
        out.extend(rule_profit_trail(m, threshold_pct=config.profit_trail_pct))

    # sort: critical first, then warn, then info; within same severity,
    # tighter SL distance comes first (most urgent risk on top)
    def _sort_key(a):
        # primary: severity descending (higher = first)
        # secondary: distance_pct ascending (smaller distance = first)
        dist = a.details.get("distance_pct") if a.details else None
        # Use a large sentinel for alerts without distance so they sort
        # to the bottom within their severity tier
        dist_key = abs(float(dist)) if dist is not None else 1e9
        return (-a.severity, dist_key)
    out.sort(key=_sort_key)
    return out
