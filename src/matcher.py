"""Match aggregated HL positions against recorded weekly decisions.

A position is 'tracked' if there's a Decision within lookback window for the same
coin, same side (long), with entry within ±entry_tolerance_pct and size within
±size_tolerance_pct of expected_size.

Anything else is 'orphan' — user opened it manually (or recommendation is too old).
Orphans still get SL/regime alerts in Phase 1.3, just no time stop or EMA20 rules.

Tolerances default:
- entry: ±2%  (rec entry $80,000 matches actual entry $78,400-$81,600)
- size:  ±15% (rec size 0.0025 BTC matches 0.002125-0.002875)

Size tolerance is wider than entry because actual position sizing depends on
available margin and what user typed in HL UI; weekly bot just recommends.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from src.decisions_log import Decision
from src.portfolio import AggregatedPerpPosition


DEFAULT_ENTRY_TOLERANCE_PCT = 2.0
DEFAULT_SIZE_TOLERANCE_PCT = 15.0


@dataclass(frozen=True)
class MatchResult:
    position: AggregatedPerpPosition
    decision: Optional[Decision]
    status: str                              # "tracked" | "orphan"
    days_in_position: Optional[int] = None   # only when tracked


def _entry_diff_pct(pos_entry: float, dec_entry: float) -> float:
    if dec_entry <= 0:
        return float("inf")
    return abs(pos_entry - dec_entry) / dec_entry * 100


def _size_diff_pct(actual_abs_size: float, expected_size: float) -> float:
    if expected_size <= 0:
        return float("inf")
    return abs(actual_abs_size - expected_size) / expected_size * 100


def match_positions(
    positions: list[AggregatedPerpPosition],
    decisions: list[Decision],
    now: Optional[datetime] = None,
    entry_tolerance_pct: float = DEFAULT_ENTRY_TOLERANCE_PCT,
    size_tolerance_pct: float = DEFAULT_SIZE_TOLERANCE_PCT,
) -> list[MatchResult]:
    """For each position, find the best matching decision or mark orphan."""
    now = now or datetime.now(timezone.utc)

    # bucket decisions by coin for O(1) lookup
    by_coin: dict[str, list[Decision]] = {}
    for d in decisions:
        by_coin.setdefault(d.coin, []).append(d)

    results: list[MatchResult] = []
    for pos in positions:
        # weekly bot is long-only; short positions are always manual
        if pos.side != "long":
            results.append(MatchResult(position=pos, decision=None, status="orphan"))
            continue

        candidates = by_coin.get(pos.coin, [])
        actual_size = abs(pos.net_size)

        # filter to within tolerances
        viable = [
            d for d in candidates
            if _entry_diff_pct(pos.weighted_entry, d.entry) <= entry_tolerance_pct
            and _size_diff_pct(actual_size, d.expected_size) <= size_tolerance_pct
        ]

        if not viable:
            results.append(MatchResult(position=pos, decision=None, status="orphan"))
            continue

        # pick closest by entry-price distance; ties broken by most recent
        viable.sort(key=lambda d: (_entry_diff_pct(pos.weighted_entry, d.entry), -d.ts.timestamp()))
        best = viable[0]

        # ensure best.ts is timezone-aware before subtracting from `now`
        ts = best.ts if best.ts.tzinfo else best.ts.replace(tzinfo=timezone.utc)
        days_in_position = (now - ts).days

        results.append(MatchResult(
            position=pos,
            decision=best,
            status="tracked",
            days_in_position=days_in_position,
        ))

    return results
