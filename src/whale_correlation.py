"""Whale correlation engine — detect signals from cross-whale fills.

Four signal types:
  CLUSTER   3+ scored whales open same side on same whitelist coin
  OVERLAP   high-WR whale opens same side as user's current position
  NEW_OPEN  high-WR whale opens fresh position on a whitelist coin
  FLIP      one whale: Close X long -> Open X short (or reverse) on same coin

Anti-noise filters baked in:
  - whitelist gate: only coins in user's whitelist surface signals
  - min_notional gate: ignore fills below $50k (default)
  - score gate: whales with status != 'ok' (insufficient_data) never count
  - winrate gate: per-rule thresholds (NEW_OPEN strictest, OVERLAP looser)
  - per-coin winrate preferred over global when ≥5 trades on that coin
  - dedup: same (rule, whale, coin) within 24h fires once; caller persists
    `seen_signals` across runs to enforce this
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from src.portfolio import AggregatedPerpPosition
from src.whale_scoring import WhaleScore, OK
from src.whale_tracker import WhaleFill


# Signal rule names — exported so renderers / tests / dedup state can refer to them
SIG_CLUSTER = "WHALE_CLUSTER"
SIG_OVERLAP = "WHALE_OVERLAP"
SIG_NEW_OPEN = "WHALE_NEW_OPEN"
SIG_FLIP = "WHALE_FLIP"


# Severity hints — matches monitor_rules constants but kept local to avoid coupling
SEV_INFO = 1
SEV_WARN = 2
SEV_CRITICAL = 3


@dataclass(frozen=True)
class Signal:
    rule: str
    severity: int
    coin: str
    message: str
    details: dict


@dataclass
class CorrelationConfig:
    min_notional_usd: float = 50_000.0       # ignore fills below this
    cluster_min_whales: int = 3
    cluster_window_minutes: int = 240        # 4h
    overlap_min_winrate: float = 0.45
    new_open_min_winrate: float = 0.55
    new_open_min_notional_usd: float = 100_000.0   # stricter than CLUSTER
    flip_min_winrate: float = 0.50
    flip_window_minutes: int = 240

    # Phase 3.2: focus coins get softer thresholds + bumped severity.
    # Default empty = Phase 2 behavior (no special treatment).
    focus_coins: frozenset[str] = field(default_factory=frozenset)
    focus_cluster_min_whales: int = 2
    focus_min_notional_usd: float = 30_000.0
    focus_new_open_min_winrate: float = 0.50


# --------------------------------------------------------- side derivation

def _side_from_direction(direction: str) -> Optional[str]:
    """'Open Long' -> 'long', 'Open Short' -> 'short', else None (close)."""
    if direction == "Open Long":
        return "long"
    if direction == "Open Short":
        return "short"
    return None


def _is_open(direction: str) -> bool:
    return direction.startswith("Open ")


def _is_close(direction: str) -> bool:
    return direction.startswith("Close ")


def _close_side(direction: str) -> Optional[str]:
    """'Close Long' -> 'long', 'Close Short' -> 'short', else None."""
    if direction == "Close Long":
        return "long"
    if direction == "Close Short":
        return "short"
    return None


# ------------------------------------------------------- scoring lookups

def _whale_is_scored(scores: dict[str, WhaleScore], whale: str) -> bool:
    s = scores.get(whale)
    return s is not None and s.status == OK


def _effective_winrate(scores: dict[str, WhaleScore], whale: str, coin: str) -> float:
    """Per-coin winrate if available (>=5 trades on that coin), else global."""
    s = scores.get(whale)
    if s is None:
        return 0.0
    cs = s.by_coin.get(coin)
    if cs is not None:
        return cs.win_rate
    return s.win_rate


# --------------------------------------------------------------- CLUSTER

def detect_cluster(
    fills: list[WhaleFill],
    scores: dict[str, WhaleScore],
    whitelist: set[str],
    config: CorrelationConfig,
) -> list[Signal]:
    """3+ scored whales opening the same side on the same whitelist coin.
    Focus coins (CorrelationConfig.focus_coins) need only 2 whales and
    use the relaxed notional floor; their signals are SEV_CRITICAL."""
    # group: (coin, side) -> set of whales
    by_coin_side: dict[tuple[str, str], set[str]] = {}
    for f in fills:
        if f.coin not in whitelist:
            continue
        is_focus = f.coin in config.focus_coins
        floor = config.focus_min_notional_usd if is_focus else config.min_notional_usd
        if f.notional_usd < floor:
            continue
        if not _whale_is_scored(scores, f.whale):
            continue
        side = _side_from_direction(f.direction)
        if side is None:
            continue
        by_coin_side.setdefault((f.coin, side), set()).add(f.whale)

    out: list[Signal] = []
    for (coin, side), whales in by_coin_side.items():
        is_focus = coin in config.focus_coins
        min_whales = (
            config.focus_cluster_min_whales if is_focus else config.cluster_min_whales
        )
        if len(whales) < min_whales:
            continue
        severity = SEV_CRITICAL if is_focus else SEV_WARN
        focus_marker = "🎯 " if is_focus else ""
        out.append(Signal(
            rule=SIG_CLUSTER,
            severity=severity,
            coin=coin,
            message=f"{focus_marker}{coin}: {len(whales)} китов открыли {side.upper()}",
            details={
                "coin": coin,
                "direction": side,
                "whale_count": len(whales),
                "whales": sorted(whales),
                "focus": is_focus,
            },
        ))
    return out


# --------------------------------------------------------------- OVERLAP

def detect_overlap(
    fills: list[WhaleFill],
    scores: dict[str, WhaleScore],
    user_positions: list[AggregatedPerpPosition],
    config: CorrelationConfig,
) -> list[Signal]:
    """High-WR whale opens same side as the user's current position."""
    user_sides: dict[str, str] = {
        p.coin: p.side for p in user_positions
        if p.side in ("long", "short")
    }
    out: list[Signal] = []
    for f in fills:
        if f.coin not in user_sides:
            continue
        if f.notional_usd < config.min_notional_usd:
            continue
        if not _whale_is_scored(scores, f.whale):
            continue
        whale_side = _side_from_direction(f.direction)
        if whale_side is None:
            continue  # close direction — not a confirmation
        if whale_side != user_sides[f.coin]:
            continue  # opposite — by spec we don't surface that
        wr = _effective_winrate(scores, f.whale, f.coin)
        if wr < config.overlap_min_winrate:
            continue
        out.append(Signal(
            rule=SIG_OVERLAP,
            severity=SEV_INFO,
            coin=f.coin,
            message=f"{f.coin}: кит {f.whale[:10]}… подтверждает {whale_side.upper()} (WR {wr:.0%})",
            details={
                "coin": f.coin,
                "whale": f.whale,
                "whale_side": whale_side,
                "user_side": user_sides[f.coin],
                "winrate_used": wr,
                "notional_usd": f.notional_usd,
            },
        ))
    return out


# --------------------------------------------------------------- NEW_OPEN

def detect_new_open(
    fills: list[WhaleFill],
    scores: dict[str, WhaleScore],
    whitelist: set[str],
    config: CorrelationConfig,
) -> list[Signal]:
    """Solo high-WR whale opening a fresh position on a whitelist coin."""
    out: list[Signal] = []
    for f in fills:
        if f.coin not in whitelist:
            continue
        if not _is_open(f.direction):
            continue
        is_focus = f.coin in config.focus_coins
        notional_floor = (
            config.focus_min_notional_usd if is_focus else config.new_open_min_notional_usd
        )
        if f.notional_usd < notional_floor:
            continue
        if not _whale_is_scored(scores, f.whale):
            continue
        wr_threshold = (
            config.focus_new_open_min_winrate if is_focus else config.new_open_min_winrate
        )
        wr = _effective_winrate(scores, f.whale, f.coin)
        if wr < wr_threshold:
            continue
        side = _side_from_direction(f.direction)
        severity = SEV_WARN if is_focus else SEV_INFO
        focus_marker = "🎯 " if is_focus else ""
        out.append(Signal(
            rule=SIG_NEW_OPEN,
            severity=severity,
            coin=f.coin,
            message=(
                f"{focus_marker}{f.coin}: кит {f.whale[:10]}… открыл "
                f"{side.upper()} ${f.notional_usd:,.0f} (WR {wr:.0%})"
            ),
            details={
                "coin": f.coin,
                "whale": f.whale,
                "direction": side,
                "notional_usd": f.notional_usd,
                "winrate_used": wr,
                "tid": f.tid,
                "focus": is_focus,
            },
        ))
    return out


# ------------------------------------------------------------------ FLIP

def detect_flip(
    fills: list[WhaleFill],
    scores: dict[str, WhaleScore],
    whitelist: set[str],
    config: CorrelationConfig,
) -> list[Signal]:
    """Per whale per coin: Close X side, then Open opposite side, in time order."""
    # group by (whale, coin)
    by_pair: dict[tuple[str, str], list[WhaleFill]] = {}
    for f in fills:
        if f.coin not in whitelist:
            continue
        if not _whale_is_scored(scores, f.whale):
            continue
        wr = _effective_winrate(scores, f.whale, f.coin)
        if wr < config.flip_min_winrate:
            continue
        by_pair.setdefault((f.whale, f.coin), []).append(f)

    out: list[Signal] = []
    for (whale, coin), pair_fills in by_pair.items():
        is_focus = coin in config.focus_coins
        # sort by time, walk looking for close followed by opposite open
        ordered = sorted(pair_fills, key=lambda x: x.time_ms)
        last_closed_side: Optional[str] = None
        last_close_time: Optional[int] = None
        for f in ordered:
            close_side = _close_side(f.direction)
            if close_side is not None:
                last_closed_side = close_side
                last_close_time = f.time_ms
                continue
            open_side = _side_from_direction(f.direction)
            if open_side is None or last_closed_side is None:
                continue
            if open_side != last_closed_side:
                severity = SEV_CRITICAL if is_focus else SEV_WARN
                focus_marker = "🎯 " if is_focus else ""
                # we have flip: was last_closed_side, now open_side
                out.append(Signal(
                    rule=SIG_FLIP,
                    severity=severity,
                    coin=coin,
                    message=(
                        f"{focus_marker}{coin}: кит {whale[:10]}… перевернулся "
                        f"{last_closed_side.upper()} → {open_side.upper()}"
                    ),
                    details={
                        "coin": coin,
                        "whale": whale,
                        "from_side": last_closed_side,
                        "to_side": open_side,
                        "close_time_ms": last_close_time,
                        "open_time_ms": f.time_ms,
                        "notional_usd": f.notional_usd,
                        "focus": is_focus,
                    },
                ))
                last_closed_side = None  # consumed
    return out


# ------------------------------------------------------- coordinator

def _dedup_key(s: Signal) -> tuple[str, str, str]:
    """24h dedup key: (rule, whale_or_empty, coin)."""
    return (s.rule, s.details.get("whale", ""), s.coin)


def detect_all(
    fills: list[WhaleFill],
    scores: dict[str, WhaleScore],
    user_positions: list[AggregatedPerpPosition],
    whitelist: set[str],
    config: CorrelationConfig,
    seen_signals: Optional[set[tuple[str, str, str]]] = None,
) -> list[Signal]:
    """Run every detector. Suppress (rule, whale, coin) tuples already seen.

    Caller passes the persisted seen_signals set so we don't re-alert across
    runs in the 24h dedup window.
    """
    raw: list[Signal] = []
    raw.extend(detect_cluster(fills, scores, whitelist, config))
    raw.extend(detect_overlap(fills, scores, user_positions, config))
    raw.extend(detect_new_open(fills, scores, whitelist, config))
    raw.extend(detect_flip(fills, scores, whitelist, config))

    if seen_signals is None:
        seen_signals = set()

    out: list[Signal] = []
    for s in raw:
        k = _dedup_key(s)
        if k in seen_signals:
            continue
        out.append(s)
    # sort: critical > warn > info, keep stable inside tier
    out.sort(key=lambda s: -s.severity)
    return out
