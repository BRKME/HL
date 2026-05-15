"""Tests for src/whale_correlation.py — detect cross-whale signals from fills."""
from datetime import datetime, timedelta, timezone

import pytest

from src.whale_correlation import (
    Signal,
    CorrelationConfig,
    detect_cluster,
    detect_overlap,
    detect_new_open,
    detect_flip,
    detect_all,
    SIG_CLUSTER,
    SIG_OVERLAP,
    SIG_NEW_OPEN,
    SIG_FLIP,
)
from src.whale_scoring import WhaleScore, CoinStats, OK, INSUFFICIENT_DATA
from src.whale_tracker import WhaleFill
from src.portfolio import AggregatedPerpPosition


NOW = datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc)


# ---------- helpers ----------

def _fill(
    whale="0xabc", coin="BTC", direction="Open Long", notional=100_000.0,
    minutes_ago=30, tid=1,
) -> WhaleFill:
    t = NOW - timedelta(minutes=minutes_ago)
    return WhaleFill(
        whale=whale.lower(), coin=coin,
        side="B" if "Long" in direction and "Open" in direction or "Short" in direction and "Close" in direction else "A",
        direction=direction,
        size=notional / 63000.0, price=63000.0, notional_usd=notional,
        tid=tid, time_ms=int(t.timestamp() * 1000),
        closed_pnl=0.0 if "Open" in direction else 100.0,
        crossed=True, oid=tid,
    )


def _good_score(whale="0xabc", win_rate=0.65, coin_wr=None) -> WhaleScore:
    by_coin = {}
    if coin_wr:
        for c, wr in coin_wr.items():
            by_coin[c] = CoinStats(coin=c, closed_trades=8, win_rate=wr, total_pnl=1000, avg_pnl=125)
    return WhaleScore(
        whale=whale.lower(), status=OK, closed_trades=20, win_rate=win_rate,
        total_pnl=10000, avg_pnl=500, best_trade=2000, worst_trade=-500,
        window_days=30, by_coin=by_coin,
    )


def _weak_score(whale="0xabc") -> WhaleScore:
    return WhaleScore(
        whale=whale.lower(), status=INSUFFICIENT_DATA, closed_trades=3,
        win_rate=0.33, total_pnl=-100, avg_pnl=-33, best_trade=10, worst_trade=-50,
        window_days=30,
    )


def _user_pos(coin="BTC", size=0.5) -> AggregatedPerpPosition:
    return AggregatedPerpPosition(
        coin=coin, net_size=size, weighted_entry=63000.0, total_pnl=0.0,
        contributors=[("main", size)], avg_leverage=10.0,
        max_liquidation_distance_pct=20.0,
    )


# ---------- CLUSTER ----------

def test_cluster_triggers_with_3_whales_same_side():
    fills = [
        _fill(whale="0x111", coin="ETH", direction="Open Long", tid=1, minutes_ago=10),
        _fill(whale="0x222", coin="ETH", direction="Open Long", tid=2, minutes_ago=20),
        _fill(whale="0x333", coin="ETH", direction="Open Long", tid=3, minutes_ago=30),
    ]
    scores = {f.whale: _good_score(f.whale) for f in fills}
    signals = detect_cluster(fills, scores, whitelist={"BTC", "ETH"}, config=CorrelationConfig())
    assert len(signals) == 1
    assert signals[0].rule == SIG_CLUSTER
    assert signals[0].coin == "ETH"
    assert signals[0].details["direction"] == "long"
    assert signals[0].details["whale_count"] == 3


def test_cluster_silent_with_only_2_whales():
    fills = [
        _fill(whale="0x111", coin="ETH", direction="Open Long", tid=1),
        _fill(whale="0x222", coin="ETH", direction="Open Long", tid=2),
    ]
    scores = {f.whale: _good_score(f.whale) for f in fills}
    signals = detect_cluster(fills, scores, whitelist={"ETH"}, config=CorrelationConfig())
    assert signals == []


def test_cluster_dedups_same_whale_multiple_fills():
    """One whale opening ETH long twice in the window is not a cluster of 2."""
    fills = [
        _fill(whale="0x111", coin="ETH", direction="Open Long", tid=1, minutes_ago=10),
        _fill(whale="0x111", coin="ETH", direction="Open Long", tid=2, minutes_ago=20),
        _fill(whale="0x111", coin="ETH", direction="Open Long", tid=3, minutes_ago=30),
    ]
    scores = {f.whale: _good_score(f.whale) for f in fills}
    signals = detect_cluster(fills, scores, whitelist={"ETH"}, config=CorrelationConfig())
    assert signals == []


def test_cluster_separates_long_from_short():
    """2 long + 1 short on ETH != cluster on any single side."""
    fills = [
        _fill(whale="0x111", coin="ETH", direction="Open Long", tid=1),
        _fill(whale="0x222", coin="ETH", direction="Open Long", tid=2),
        _fill(whale="0x333", coin="ETH", direction="Open Short", tid=3),
    ]
    scores = {f.whale: _good_score(f.whale) for f in fills}
    signals = detect_cluster(fills, scores, whitelist={"ETH"}, config=CorrelationConfig())
    assert signals == []


def test_cluster_filters_by_whitelist():
    fills = [
        _fill(whale=f"0x{i}", coin="DOGE", direction="Open Long", tid=i)
        for i in range(1, 4)
    ]
    scores = {f.whale: _good_score(f.whale) for f in fills}
    signals = detect_cluster(fills, scores, whitelist={"BTC", "ETH"}, config=CorrelationConfig())
    assert signals == []


def test_cluster_skips_whales_with_insufficient_data():
    """Only count whales we can score — newcomers don't count toward cluster."""
    fills = [
        _fill(whale="0x111", coin="ETH", direction="Open Long", tid=1),
        _fill(whale="0x222", coin="ETH", direction="Open Long", tid=2),
        _fill(whale="0x333", coin="ETH", direction="Open Long", tid=3),
    ]
    scores = {
        "0x111": _good_score("0x111"),
        "0x222": _good_score("0x222"),
        "0x333": _weak_score("0x333"),
    }
    signals = detect_cluster(fills, scores, whitelist={"ETH"}, config=CorrelationConfig())
    assert signals == []


def test_cluster_filters_below_min_notional():
    fills = [
        _fill(whale=f"0x{i}", coin="ETH", direction="Open Long", notional=10_000, tid=i)
        for i in range(1, 4)
    ]
    scores = {f.whale: _good_score(f.whale) for f in fills}
    cfg = CorrelationConfig(min_notional_usd=50_000)
    signals = detect_cluster(fills, scores, whitelist={"ETH"}, config=cfg)
    assert signals == []


# ---------- OVERLAP ----------

def test_overlap_triggers_when_whale_opens_user_held_coin_same_side():
    """User long BTC; high-WR whale just opened BTC long -> confirms thesis."""
    fills = [_fill(whale="0x111", coin="BTC", direction="Open Long", tid=1)]
    scores = {"0x111": _good_score("0x111", win_rate=0.65)}
    user_positions = [_user_pos(coin="BTC", size=0.5)]
    signals = detect_overlap(fills, scores, user_positions, config=CorrelationConfig())
    assert len(signals) == 1
    assert signals[0].rule == SIG_OVERLAP
    assert signals[0].coin == "BTC"
    assert signals[0].details["user_side"] == "long"
    assert signals[0].details["whale_side"] == "long"


def test_overlap_silent_when_user_no_position_on_coin():
    fills = [_fill(whale="0x111", coin="ETH", direction="Open Long", tid=1)]
    scores = {"0x111": _good_score("0x111")}
    user_positions = [_user_pos(coin="BTC", size=0.5)]
    signals = detect_overlap(fills, scores, user_positions, config=CorrelationConfig())
    assert signals == []


def test_overlap_silent_when_whale_winrate_too_low():
    """Don't surface overlaps from mediocre whales — too noisy."""
    fills = [_fill(whale="0x111", coin="BTC", direction="Open Long", tid=1)]
    scores = {"0x111": _good_score("0x111", win_rate=0.40)}  # below default 0.45
    user_positions = [_user_pos(coin="BTC", size=0.5)]
    signals = detect_overlap(fills, scores, user_positions, config=CorrelationConfig())
    assert signals == []


def test_overlap_silent_when_whale_opposite_side():
    """User long BTC, whale opened SHORT BTC — per spec, no OPPOSITE alert."""
    fills = [_fill(whale="0x111", coin="BTC", direction="Open Short", tid=1)]
    scores = {"0x111": _good_score("0x111")}
    user_positions = [_user_pos(coin="BTC", size=0.5)]
    signals = detect_overlap(fills, scores, user_positions, config=CorrelationConfig())
    assert signals == []


def test_overlap_uses_per_coin_winrate_when_available():
    """Whale's BTC-specific win-rate beats their global win-rate."""
    fills = [_fill(whale="0x111", coin="BTC", direction="Open Long", tid=1)]
    # global 0.50 but BTC-specific 0.80 — should pass the 0.45 threshold
    scores = {"0x111": _good_score("0x111", win_rate=0.50, coin_wr={"BTC": 0.80})}
    user_positions = [_user_pos(coin="BTC", size=0.5)]
    signals = detect_overlap(fills, scores, user_positions, config=CorrelationConfig())
    assert len(signals) == 1
    assert signals[0].details["winrate_used"] == pytest.approx(0.80)


def test_overlap_ignores_close_direction():
    """Whale CLOSING long BTC isn't a confirmation — it's profit-taking."""
    fills = [_fill(whale="0x111", coin="BTC", direction="Close Long", tid=1)]
    scores = {"0x111": _good_score("0x111")}
    user_positions = [_user_pos(coin="BTC", size=0.5)]
    signals = detect_overlap(fills, scores, user_positions, config=CorrelationConfig())
    assert signals == []


# ---------- NEW_OPEN ----------

def test_new_open_triggers_for_high_winrate_whale():
    fills = [_fill(whale="0x111", coin="ETH", direction="Open Long",
                   notional=500_000, tid=1)]
    scores = {"0x111": _good_score("0x111", win_rate=0.70)}
    signals = detect_new_open(fills, scores, whitelist={"ETH"}, config=CorrelationConfig())
    assert len(signals) == 1
    assert signals[0].rule == SIG_NEW_OPEN
    assert signals[0].details["direction"] == "long"
    assert signals[0].details["notional_usd"] == 500_000


def test_new_open_silent_for_low_winrate_whale():
    fills = [_fill(whale="0x111", coin="ETH", direction="Open Long", tid=1)]
    scores = {"0x111": _good_score("0x111", win_rate=0.40)}
    signals = detect_new_open(fills, scores, whitelist={"ETH"}, config=CorrelationConfig())
    assert signals == []


def test_new_open_silent_for_close_direction():
    fills = [_fill(whale="0x111", coin="ETH", direction="Close Long", tid=1)]
    scores = {"0x111": _good_score("0x111")}
    signals = detect_new_open(fills, scores, whitelist={"ETH"}, config=CorrelationConfig())
    assert signals == []


def test_new_open_requires_high_winrate_above_overlap_threshold():
    """NEW_OPEN should use a stricter winrate threshold than OVERLAP
    since there's no user-position confirmation."""
    fills = [_fill(whale="0x111", coin="ETH", direction="Open Long", tid=1)]
    scores = {"0x111": _good_score("0x111", win_rate=0.50)}  # ok for OVERLAP, weak for NEW_OPEN
    cfg = CorrelationConfig(new_open_min_winrate=0.60)
    signals = detect_new_open(fills, scores, whitelist={"ETH"}, config=cfg)
    assert signals == []


def test_new_open_filters_whitelist():
    fills = [_fill(whale="0x111", coin="DOGE", direction="Open Long", tid=1)]
    scores = {"0x111": _good_score("0x111")}
    signals = detect_new_open(fills, scores, whitelist={"BTC", "ETH"}, config=CorrelationConfig())
    assert signals == []


# ---------- FLIP ----------

def test_flip_triggers_close_long_then_open_short_same_coin():
    """Close Long followed by Open Short on same coin = flip."""
    fills = [
        _fill(whale="0x111", coin="BTC", direction="Close Long",
              notional=200_000, minutes_ago=60, tid=1),
        _fill(whale="0x111", coin="BTC", direction="Open Short",
              notional=200_000, minutes_ago=50, tid=2),
    ]
    scores = {"0x111": _good_score("0x111", win_rate=0.65)}
    signals = detect_flip(fills, scores, whitelist={"BTC"}, config=CorrelationConfig())
    assert len(signals) == 1
    assert signals[0].rule == SIG_FLIP
    assert signals[0].details["from_side"] == "long"
    assert signals[0].details["to_side"] == "short"


def test_flip_triggers_close_short_then_open_long():
    fills = [
        _fill(whale="0x111", coin="ETH", direction="Close Short",
              minutes_ago=60, tid=1),
        _fill(whale="0x111", coin="ETH", direction="Open Long",
              minutes_ago=50, tid=2),
    ]
    scores = {"0x111": _good_score("0x111")}
    signals = detect_flip(fills, scores, whitelist={"ETH"}, config=CorrelationConfig())
    assert len(signals) == 1
    assert signals[0].details["from_side"] == "short"
    assert signals[0].details["to_side"] == "long"


def test_flip_silent_close_without_subsequent_open():
    """Just closing — not a flip, just profit-taking or exit."""
    fills = [_fill(whale="0x111", coin="BTC", direction="Close Long", tid=1)]
    scores = {"0x111": _good_score("0x111")}
    signals = detect_flip(fills, scores, whitelist={"BTC"}, config=CorrelationConfig())
    assert signals == []


def test_flip_silent_when_close_and_open_on_different_coins():
    """Close BTC long then open ETH short ≠ flip — different positions."""
    fills = [
        _fill(whale="0x111", coin="BTC", direction="Close Long", minutes_ago=60, tid=1),
        _fill(whale="0x111", coin="ETH", direction="Open Short", minutes_ago=50, tid=2),
    ]
    scores = {"0x111": _good_score("0x111")}
    signals = detect_flip(fills, scores, whitelist={"BTC", "ETH"}, config=CorrelationConfig())
    assert signals == []


def test_flip_silent_when_open_before_close():
    """Open Short happened before Close Long — that's adding a hedge, not a flip."""
    fills = [
        _fill(whale="0x111", coin="BTC", direction="Open Short", minutes_ago=60, tid=1),
        _fill(whale="0x111", coin="BTC", direction="Close Long", minutes_ago=50, tid=2),
    ]
    scores = {"0x111": _good_score("0x111")}
    signals = detect_flip(fills, scores, whitelist={"BTC"}, config=CorrelationConfig())
    assert signals == []


def test_flip_filters_whitelist():
    fills = [
        _fill(whale="0x111", coin="DOGE", direction="Close Long", minutes_ago=60, tid=1),
        _fill(whale="0x111", coin="DOGE", direction="Open Short", minutes_ago=50, tid=2),
    ]
    scores = {"0x111": _good_score("0x111")}
    signals = detect_flip(fills, scores, whitelist={"BTC"}, config=CorrelationConfig())
    assert signals == []


# ---------- detect_all coordinator ----------

def test_detect_all_runs_all_rules():
    fills = [
        # Cluster on ETH
        _fill(whale="0x111", coin="ETH", direction="Open Long", tid=1),
        _fill(whale="0x222", coin="ETH", direction="Open Long", tid=2),
        _fill(whale="0x333", coin="ETH", direction="Open Long", tid=3),
        # Overlap on BTC (user holds it)
        _fill(whale="0x444", coin="BTC", direction="Open Long", tid=4),
    ]
    scores = {
        "0x111": _good_score("0x111"),
        "0x222": _good_score("0x222"),
        "0x333": _good_score("0x333"),
        "0x444": _good_score("0x444"),
    }
    user_positions = [_user_pos(coin="BTC", size=0.5)]
    signals = detect_all(
        fills, scores, user_positions, whitelist={"BTC", "ETH"},
        config=CorrelationConfig(),
    )
    rules = {s.rule for s in signals}
    assert SIG_CLUSTER in rules
    assert SIG_OVERLAP in rules


def test_detect_all_empty_with_no_data():
    signals = detect_all([], {}, [], whitelist=set(), config=CorrelationConfig())
    assert signals == []


def test_detect_all_24h_dedup_suppresses_repeat_signals():
    """Same whale, same coin, same rule within 24h should fire once.
    The seen_signals param simulates yesterday's persisted state."""
    fills = [_fill(whale="0x111", coin="ETH", direction="Open Long", tid=1)]
    scores = {"0x111": _good_score("0x111", win_rate=0.70)}
    cfg = CorrelationConfig(new_open_min_winrate=0.60)

    # First run — signal fires
    seen: set[tuple[str, str, str]] = set()
    signals1 = detect_all(fills, scores, [], whitelist={"ETH"}, config=cfg, seen_signals=seen)
    assert len(signals1) == 1
    # seen_signals updated in-place after first run
    for s in signals1:
        seen.add((s.rule, s.details.get("whale", ""), s.coin))

    # Second run with same data — suppressed
    signals2 = detect_all(fills, scores, [], whitelist={"ETH"}, config=cfg, seen_signals=seen)
    assert signals2 == []
