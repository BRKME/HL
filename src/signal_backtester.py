"""Signal backtester — Phase 4.

Takes accumulated whale signals from state/whale_signals.jsonl and measures
what actually happened to the price after each signal across multiple
horizons (6h / 24h / 48h / 7d). Groups by (coin × rule × direction),
computes win-rate and average return per group, and produces a Telegram
report.

Threshold for 'actionable alpha': WR >= 60% AND N >= 10 events at the 24h
horizon. Anything else is either noise or not enough data.

This is a read-only analytics module — does NOT trade and does NOT mutate
any state. Intended to run weekly (Fri/Sat) via workflow_dispatch or cron.
"""
from __future__ import annotations

import html
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("signal_backtester")

# Horizons we measure (hours)
HORIZONS_HOURS = [6, 24, 48, 168]  # 168h = 7 days

# Actionability thresholds — what makes a group "alpha"
MIN_EVENTS_ACTIONABLE = 10
MIN_WIN_RATE_ACTIONABLE = 0.60
PRIMARY_HORIZON = 24  # The horizon used for headline WR


# ----------------------------------------------------- models

@dataclass(frozen=True)
class Signal:
    ts: datetime
    rule: str
    coin: str
    severity: int
    details: dict


@dataclass
class SignalOutcome:
    """What actually happened after a single signal."""
    signal: Signal
    direction: str
    entry_price: float
    returns_pct: dict  # horizon_hours -> pct return (None if no data)
    max_dd_pct_24h: Optional[float] = None
    max_dd_pct_168h: Optional[float] = None


@dataclass
class BacktestGroup:
    """Aggregated outcomes for one (coin, rule, direction) group."""
    coin: str
    rule: str
    direction: str
    n_events: int
    win_rate: dict          # horizon_hours -> fraction
    avg_return_pct: dict    # horizon_hours -> pct
    max_dd_pct: dict        # horizon_hours -> worst DD seen across events

    def is_actionable(self) -> bool:
        wr = self.win_rate.get(PRIMARY_HORIZON, 0.0)
        return (self.n_events >= MIN_EVENTS_ACTIONABLE
                and wr >= MIN_WIN_RATE_ACTIONABLE)

    def headline_wr(self) -> float:
        return self.win_rate.get(PRIMARY_HORIZON, 0.0)


# ----------------------------------------------------- IO

def load_signals(path: Path) -> list[Signal]:
    """Read signals from JSONL, skip malformed lines, return parsed list."""
    path = Path(path)
    if not path.exists():
        return []
    out: list[Signal] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_str = row.get("run_ts", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    continue
                out.append(Signal(
                    ts=ts,
                    rule=str(row.get("rule", "")),
                    coin=str(row.get("coin", "")),
                    severity=int(row.get("severity", 0)),
                    details=row.get("details") or {},
                ))
    except OSError:
        return []
    return out


# ----------------------------------------------------- direction extraction

def extract_direction(sig: Signal) -> Optional[str]:
    """Get the trade direction implied by the signal.

    FLIP: to_side
    OVERLAP: whale_side
    NEW_OPEN, CLUSTER: direction
    Otherwise: None (signal has no actionable direction, e.g. NEW_ENTRANT
    just says 'whale appeared in top', not 'go long').
    """
    d = sig.details or {}
    if sig.rule == "WHALE_FLIP":
        side = d.get("to_side")
    elif sig.rule == "WHALE_OVERLAP":
        side = d.get("whale_side")
    elif sig.rule in ("WHALE_NEW_OPEN", "WHALE_CLUSTER"):
        side = d.get("direction")
    else:
        return None
    if side in ("long", "short"):
        return side
    return None


# ----------------------------------------------------- outcome measurement

def _find_entry_candle(candles: list[dict], sig_ts_ms: int) -> Optional[dict]:
    """Find the candle whose timestamp is just at or before sig_ts_ms.

    HL D1/H1 candles have 't' = open time in ms. We pick the candle whose
    open <= sig_ts_ms < open + interval. If signal is before any candle,
    return None. If signal is after the last candle, also None — we can't
    measure something we don't have data for.
    """
    if not candles:
        return None
    # find the latest candle whose t <= sig_ts_ms
    entry = None
    for c in candles:
        if c.get("t", 0) <= sig_ts_ms:
            entry = c
        else:
            break
    return entry


def _max_dd_for_window(
    entry_price: float, direction: str,
    candles: list[dict], start_ms: int, end_ms: int,
) -> Optional[float]:
    """Worst adverse move (%) within window, from entry_price.

    For long: lowest low in window vs entry → negative pct.
    For short: highest high in window vs entry → negative pct.
    Returns None if no candles in window.
    """
    in_window = [c for c in candles if start_ms <= c.get("t", 0) <= end_ms]
    if not in_window:
        return None
    if direction == "long":
        worst_low = min(c.get("l", c.get("c", entry_price)) for c in in_window)
        return (worst_low - entry_price) / entry_price * 100
    else:
        worst_high = max(c.get("h", c.get("c", entry_price)) for c in in_window)
        return -(worst_high - entry_price) / entry_price * 100


def measure_outcome(
    sig: Signal, direction: str, candles: list[dict],
) -> Optional[SignalOutcome]:
    """For one signal, compute returns at each horizon and max DD."""
    sig_ts_ms = int(sig.ts.timestamp() * 1000)
    entry = _find_entry_candle(candles, sig_ts_ms)
    if entry is None:
        return None
    entry_price = float(entry.get("c", 0))
    if entry_price <= 0:
        return None

    returns: dict[int, Optional[float]] = {}
    for h in HORIZONS_HOURS:
        target_ms = sig_ts_ms + h * 3600_000
        # Find candle whose t is closest to target_ms but >= sig
        target_candle = None
        for c in candles:
            t = c.get("t", 0)
            if t >= target_ms:
                target_candle = c
                break
        if target_candle is None:
            returns[h] = None
        else:
            close = float(target_candle.get("c", 0))
            if close <= 0:
                returns[h] = None
            else:
                raw_pct = (close - entry_price) / entry_price * 100
                # For short, profit = price went down, so flip sign
                returns[h] = raw_pct if direction == "long" else -raw_pct

    # Max DD within 24h and 168h windows
    dd_24h = _max_dd_for_window(
        entry_price, direction, candles, sig_ts_ms, sig_ts_ms + 24 * 3600_000,
    )
    dd_168h = _max_dd_for_window(
        entry_price, direction, candles, sig_ts_ms, sig_ts_ms + 168 * 3600_000,
    )

    return SignalOutcome(
        signal=sig,
        direction=direction,
        entry_price=entry_price,
        returns_pct=returns,
        max_dd_pct_24h=dd_24h,
        max_dd_pct_168h=dd_168h,
    )


# ----------------------------------------------------- grouping & aggregation

def group_signals(signals: list[Signal]) -> dict[tuple[str, str, str], list[Signal]]:
    """Group by (coin, rule, direction). Signals without a direction skipped."""
    groups: dict[tuple[str, str, str], list[Signal]] = {}
    for s in signals:
        direction = extract_direction(s)
        if direction is None:
            continue
        key = (s.coin, s.rule, direction)
        groups.setdefault(key, []).append(s)
    return groups


def _aggregate(outcomes: list[SignalOutcome]) -> tuple[dict, dict, dict]:
    """Across a list of outcomes, compute WR/avg/worst DD per horizon."""
    win_rate: dict[int, float] = {}
    avg_return: dict[int, float] = {}
    max_dd: dict[int, float] = {}

    for h in HORIZONS_HOURS:
        rets = [o.returns_pct.get(h) for o in outcomes
                if o.returns_pct.get(h) is not None]
        if not rets:
            win_rate[h] = 0.0
            avg_return[h] = 0.0
            max_dd[h] = 0.0
            continue
        wins = sum(1 for r in rets if r > 0)
        win_rate[h] = wins / len(rets)
        avg_return[h] = sum(rets) / len(rets)
        # max_dd = worst single-event return at this horizon (proxy)
        max_dd[h] = min(rets)
    return win_rate, avg_return, max_dd


def backtest(
    signals: list[Signal],
    candles_by_coin: dict[str, list[dict]],
    min_notional_usd: float = 0.0,
) -> list[BacktestGroup]:
    """Top-level: group signals, measure each, aggregate to groups.

    min_notional_usd filters out signals whose details.notional_usd is
    below this floor. Used to separate 'real' whale moves from
    micro-position noise (e.g. $174 ZEC FLIP fills that mean nothing).
    """
    # Filter by notional before grouping
    if min_notional_usd > 0:
        signals = [
            s for s in signals
            if float(s.details.get("notional_usd", 0) or 0) >= min_notional_usd
        ]
    groups = group_signals(signals)
    out: list[BacktestGroup] = []
    for (coin, rule, direction), sigs in groups.items():
        candles = candles_by_coin.get(coin, [])
        outcomes: list[SignalOutcome] = []
        for s in sigs:
            o = measure_outcome(s, direction, candles)
            if o is not None:
                outcomes.append(o)
        if not outcomes:
            continue
        wr, avg, dd = _aggregate(outcomes)
        out.append(BacktestGroup(
            coin=coin, rule=rule, direction=direction,
            n_events=len(outcomes),
            win_rate=wr, avg_return_pct=avg, max_dd_pct=dd,
        ))
    return out


# ----------------------------------------------------- render

_MOSCOW = timezone(timedelta(hours=3))

def _short_rule(rule: str) -> str:
    return rule.replace("WHALE_", "").upper()


def render_report(groups: list[BacktestGroup], now: datetime) -> str:
    """Build a Telegram-ready report."""
    msk = now.astimezone(_MOSCOW)
    head = (f"🎯 <b>Signal performance</b> — на {msk.strftime('%d %b %H:%M MSK')}\n"
            f"Threshold: WR ≥ {int(MIN_WIN_RATE_ACTIONABLE*100)}% и N ≥ "
            f"{MIN_EVENTS_ACTIONABLE} событий")

    if not groups:
        return head + "\n\nДанных нет — backtest вернул 0 групп."

    # Sort: actionable first (by N×WR desc), then by N desc
    def _sort_key(g: BacktestGroup):
        score = g.headline_wr() * g.n_events if g.is_actionable() else 0
        return (-score, -g.n_events)
    groups_sorted = sorted(groups, key=_sort_key)

    actionable_groups = [g for g in groups_sorted if g.is_actionable()]
    other_groups = [g for g in groups_sorted if not g.is_actionable()]

    parts = [head]

    if actionable_groups:
        parts.append("\n<b>🎯 Actionable (≥10 ev, ≥60% WR)</b>")
        for g in actionable_groups:
            parts.append(_render_group(g, mark_alpha=True))
    else:
        parts.append("\n<i>Actionable групп нет — пока ни одна комбинация "
                      "не набрала WR ≥ 60% при ≥ 10 событиях.</i>")

    if other_groups:
        parts.append("\n<b>Остальные группы</b>")
        # Show those with at least 3 events; trim heavy noise
        for g in other_groups:
            if g.n_events >= 3:
                parts.append(_render_group(g, mark_alpha=False))

    too_few = [g for g in groups if g.n_events < 3]
    if too_few:
        coins = sorted({g.coin for g in too_few})
        parts.append(f"\n<i>Мало данных (&lt; 3 событий): "
                      f"{', '.join(coins[:10])}{'…' if len(coins) > 10 else ''}</i>")

    return "\n".join(parts)


def _render_group(g: BacktestGroup, mark_alpha: bool = False) -> str:
    prefix = "🎯 " if mark_alpha else "  "
    wr24 = g.win_rate.get(24, 0.0) * 100
    avg24 = g.avg_return_pct.get(24, 0.0)
    dd24 = g.max_dd_pct.get(24, 0.0)
    wr168 = g.win_rate.get(168, 0.0) * 100
    avg168 = g.avg_return_pct.get(168, 0.0)
    return (
        f"{prefix}<code>{g.coin}</code> {_short_rule(g.rule)} {g.direction.upper()} — "
        f"{g.n_events} ev\n"
        f"   24h: WR {wr24:.0f}%, avg {avg24:+.1f}%, worst {dd24:+.1f}%\n"
        f"    7d: WR {wr168:.0f}%, avg {avg168:+.1f}%"
    )


# ----------------------------------------------------- multi-threshold compare

def backtest_thresholds(
    signals: list[Signal],
    candles_by_coin: dict[str, list[dict]],
    thresholds: list[float] = (0, 10_000, 50_000),
) -> dict[float, list[BacktestGroup]]:
    """Run backtest at each notional threshold. Returns {threshold: groups}."""
    return {
        t: backtest(signals, candles_by_coin, min_notional_usd=t)
        for t in thresholds
    }


def render_comparison_report(
    results_by_threshold: dict[float, list[BacktestGroup]],
    now: datetime,
) -> str:
    """Compare WR/N across notional thresholds for each (coin, rule, direction).

    Format:
      BTC FLIP SHORT
        ≥$0:    10 ev, WR 100% / 24h, avg +0.3%
        ≥$10k:   7 ev, WR 100% / 24h, avg +0.5%
        ≥$50k:   3 ev, WR 100% / 24h, avg +1.2%   ← bigger fills, bigger move

    A group "improves with size" when raising the threshold keeps WR
    high while N drops — that's evidence real whales matter, noise was
    diluting the signal.
    """
    msk = now.astimezone(_MOSCOW)
    head = (f"🎯 <b>Signal performance с фильтром по notional</b>\n"
            f"на {msk.strftime('%d %b %H:%M MSK')}\n"
            f"Threshold: WR ≥ {int(MIN_WIN_RATE_ACTIONABLE*100)}% и N ≥ "
            f"{MIN_EVENTS_ACTIONABLE} событий\n")

    # Build key set from all thresholds
    all_keys: set[tuple[str, str, str]] = set()
    for groups in results_by_threshold.values():
        for g in groups:
            all_keys.add((g.coin, g.rule, g.direction))
    if not all_keys:
        return head + "\nДанных нет."

    thresholds = sorted(results_by_threshold.keys())

    # Index for fast lookup
    by_key: dict[tuple[str, str, str], dict[float, BacktestGroup]] = {}
    for t, groups in results_by_threshold.items():
        for g in groups:
            by_key.setdefault((g.coin, g.rule, g.direction), {})[t] = g

    # Score each key by best WR×N at the strictest threshold that still has data
    def _score(key):
        best = 0.0
        for t in sorted(thresholds, reverse=True):
            g = by_key.get(key, {}).get(t)
            if g and g.n_events > 0:
                best = max(best, g.win_rate.get(24, 0) * g.n_events)
                if g.n_events >= 3:
                    break
        return -best

    sorted_keys = sorted(all_keys, key=_score)

    parts = [head]
    shown = 0
    SURVIVE_THRESHOLD = 10_000   # «настоящие киты»: сигнал должен выжить здесь
    for key in sorted_keys:
        coin, rule, direction = key
        per_t = by_key.get(key, {})
        if not per_t:
            continue
        # Показываем ТОЛЬКО сигналы, переживающие фильтр крупного notional с
        # данными — это и есть критерий «настоящих китов». Группы, схлопывающие
        # в '≥$10k: 0 ev', были шумом мелких сделок и каналу не нужны.
        survives = any(t >= SURVIVE_THRESHOLD and g.n_events >= 3
                       for t, g in per_t.items())
        if not survives:
            continue

        parts.append(f"\n<b>{html.escape(coin)} {_short_rule(rule)} {direction.upper()}</b>")
        for t in thresholds:
            g = per_t.get(t)
            label = f"≥${int(t/1000)}k" if t >= 1000 else "≥$0"
            if g is None or g.n_events == 0:
                parts.append(f"  {label:<8}: 0 ev")
                continue
            wr24 = g.win_rate.get(24, 0.0) * 100
            avg24 = g.avg_return_pct.get(24, 0.0)
            wr168 = g.win_rate.get(168, 0.0) * 100
            avg168 = g.avg_return_pct.get(168, 0.0)
            alpha = " 🎯" if g.is_actionable() else ""
            parts.append(
                f"  {label:<8}: {g.n_events} ev, "
                f"24h WR {wr24:.0f}% avg {avg24:+.1f}% • "
                f"7d WR {wr168:.0f}% avg {avg168:+.1f}%{alpha}"
            )
        shown += 1

    if shown == 0:
        return (head + "\nНи один сигнал не пережил фильтр крупного notional "
                "(≥$10k) с N≥3 — на этой неделе «настоящих» китовых паттернов "
                "не выделено. Это нормальный результат, не ошибка.")

    parts.append("\n<i>Показаны только сигналы, пережившие фильтр ≥$10k — "
                 "те, где за паттерном стоят крупные киты, а не шум мелких "
                 "сделок.</i>")
    return "\n".join(parts)
