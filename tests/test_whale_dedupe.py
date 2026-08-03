"""Whale signal deduplication (03.08.2026).

state/whale_signal_stats.json reported `ETH:SHORT n=55 wr=0.0`. Read as
written, that is 55 independent whale shorts on ETH, every one of them
wrong — an extraordinary claim. The journal says otherwise:

    104 ETH-short signals
    6 distinct whales
    5 distinct days
    one whale accounts for 53 of them, another for 41
    54 of them land on a single day, 03.07

So it is roughly two whale decisions, re-reported a hundred times as the
monitor kept seeing the same standing positions. Every copy resolves at the
same price against the same candles, so they all win or all lose together.

That inflates `n_events`, and `n_events` is half of `is_actionable()`
(N ≥ 10, WR ≥ 60%). A single lucky whale re-counted eleven times clears the
sample gate and can promote a group to actionable on one observation. The
win rate itself was never the bug; the denominator was.

One observation = one whale, one coin, one direction, one day.
"""
from datetime import datetime, timezone

from src.signal_backtester import Signal, dedupe_signals, group_signals


def _sig(whale, coin="ETH", direction="short", day=3, hour=12,
         rule="WHALE_NEW_OPEN"):
    return Signal(
        ts=datetime(2026, 7, day, hour, tzinfo=timezone.utc),
        rule=rule, coin=coin, severity=2,
        details={"coin": coin, "direction": direction, "whale": whale},
    )


def test_same_whale_same_day_collapses_to_one():
    sigs = [_sig("0xaaa", hour=h) for h in (9, 11, 13, 15)]
    assert len(dedupe_signals(sigs)) == 1


def test_earliest_signal_of_the_day_is_kept():
    """The first sighting is the one whose forward return is honest."""
    sigs = [_sig("0xaaa", hour=15), _sig("0xaaa", hour=9)]
    kept = dedupe_signals(sigs)
    assert len(kept) == 1
    assert kept[0].ts.hour == 9


def test_different_whales_are_kept_separately():
    sigs = [_sig("0xaaa"), _sig("0xbbb")]
    assert len(dedupe_signals(sigs)) == 2


def test_same_whale_different_days_are_kept():
    sigs = [_sig("0xaaa", day=3), _sig("0xaaa", day=4)]
    assert len(dedupe_signals(sigs)) == 2


def test_same_whale_opposite_directions_are_kept():
    sigs = [_sig("0xaaa", direction="short"), _sig("0xaaa", direction="long")]
    assert len(dedupe_signals(sigs)) == 2


def test_same_whale_different_coins_are_kept():
    sigs = [_sig("0xaaa", coin="ETH"), _sig("0xaaa", coin="BTC")]
    assert len(dedupe_signals(sigs)) == 2


def test_signals_without_whale_id_are_not_collapsed():
    """Unknown provenance must not be merged into a single event."""
    sigs = [_sig(None, hour=9), _sig(None, hour=15)]
    assert len(dedupe_signals(sigs)) == 2


def test_the_real_eth_shape_collapses_to_whale_days():
    """Reproduces the observed distribution: 2 whales, 2 days, ~94 copies."""
    sigs = ([_sig("0xcf5343ba", day=1, hour=h % 24) for h in range(41)]
            + [_sig("0x50b309f7", day=3, hour=h % 24) for h in range(53)])
    kept = dedupe_signals(sigs)
    assert len(kept) == 2


def test_grouping_applies_dedupe():
    sigs = [_sig("0xaaa", hour=h) for h in (9, 11, 13)] + [_sig("0xbbb")]
    groups = group_signals(sigs)
    assert sum(len(v) for v in groups.values()) == 2


def test_dedupe_preserves_directionless_skip():
    """Signals with no actionable direction are still dropped by grouping."""
    s = Signal(ts=datetime(2026, 7, 3, tzinfo=timezone.utc),
               rule="WHALE_NEW_ENTRANT", coin="ETH", severity=1,
               details={"whale": "0xaaa"})
    assert group_signals([s]) == {}
