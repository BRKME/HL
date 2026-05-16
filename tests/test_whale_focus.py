"""Tests for src/whale_correlation.py focus_coins behavior (Phase 3.2)."""
from datetime import datetime, timedelta, timezone

import pytest

from src.whale_correlation import (
    Signal,
    CorrelationConfig,
    detect_cluster,
    detect_new_open,
    detect_flip,
    detect_all,
    SIG_CLUSTER, SIG_NEW_OPEN, SIG_FLIP, SIG_OVERLAP,
    SEV_INFO, SEV_WARN, SEV_CRITICAL,
)
from src.whale_scoring import WhaleScore, CoinStats, OK
from src.whale_tracker import WhaleFill


NOW = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)


def _fill(whale, coin, direction, notional=100_000.0, tid=1, minutes_ago=30):
    t = NOW - timedelta(minutes=minutes_ago)
    return WhaleFill(
        whale=whale.lower(), coin=coin,
        side="B", direction=direction,
        size=notional / 3200.0, price=3200.0, notional_usd=notional,
        tid=tid, time_ms=int(t.timestamp() * 1000),
        closed_pnl=0.0 if "Open" in direction else 100.0,
        crossed=True, oid=tid,
    )


def _good_score(whale="0xabc", win_rate=0.55, coin_wr=None):
    by_coin = {}
    if coin_wr:
        for c, wr in coin_wr.items():
            by_coin[c] = CoinStats(coin=c, closed_trades=8, win_rate=wr, total_pnl=1000, avg_pnl=125)
    return WhaleScore(
        whale=whale.lower(), status=OK, closed_trades=20, win_rate=win_rate,
        total_pnl=10000, avg_pnl=500, best_trade=2000, worst_trade=-500,
        window_days=30, by_coin=by_coin,
    )


# ---------- CLUSTER: 2 whales is enough on focus coin ----------

def test_cluster_triggers_with_2_whales_on_focus_coin():
    """Without focus: 2 whales -> silent. With focus={ETH}: 2 whales on ETH -> fires."""
    fills = [
        _fill("0x111", "ETH", "Open Long", tid=1),
        _fill("0x222", "ETH", "Open Long", tid=2),
    ]
    scores = {f.whale: _good_score(f.whale) for f in fills}
    cfg = CorrelationConfig(focus_coins={"ETH"})
    signals = detect_cluster(fills, scores, whitelist={"ETH"}, config=cfg)
    assert len(signals) == 1
    assert signals[0].coin == "ETH"


def test_cluster_still_requires_3_whales_on_non_focus_coin():
    """Same scenario but BTC (not in focus) — still needs 3 whales."""
    fills = [
        _fill("0x111", "BTC", "Open Long", tid=1),
        _fill("0x222", "BTC", "Open Long", tid=2),
    ]
    scores = {f.whale: _good_score(f.whale) for f in fills}
    cfg = CorrelationConfig(focus_coins={"ETH"})
    signals = detect_cluster(fills, scores, whitelist={"BTC", "ETH"}, config=cfg)
    assert signals == []


def test_cluster_focus_coin_signal_is_critical_severity():
    fills = [
        _fill("0x111", "ETH", "Open Long", tid=1),
        _fill("0x222", "ETH", "Open Long", tid=2),
    ]
    scores = {f.whale: _good_score(f.whale) for f in fills}
    cfg = CorrelationConfig(focus_coins={"ETH"})
    signals = detect_cluster(fills, scores, whitelist={"ETH"}, config=cfg)
    assert signals[0].severity == SEV_CRITICAL


def test_cluster_non_focus_coin_signal_stays_warn():
    fills = [
        _fill("0x111", "BTC", "Open Long", tid=1),
        _fill("0x222", "BTC", "Open Long", tid=2),
        _fill("0x333", "BTC", "Open Long", tid=3),
    ]
    scores = {f.whale: _good_score(f.whale) for f in fills}
    cfg = CorrelationConfig(focus_coins={"ETH"})
    signals = detect_cluster(fills, scores, whitelist={"BTC"}, config=cfg)
    assert signals[0].severity == SEV_WARN


def test_cluster_focus_uses_softer_notional_threshold():
    """Default min_notional 50k; focus override 30k — $35k fills should count."""
    fills = [
        _fill("0x111", "ETH", "Open Long", notional=35_000, tid=1),
        _fill("0x222", "ETH", "Open Long", notional=35_000, tid=2),
    ]
    scores = {f.whale: _good_score(f.whale) for f in fills}
    cfg = CorrelationConfig(focus_coins={"ETH"})
    signals = detect_cluster(fills, scores, whitelist={"ETH"}, config=cfg)
    assert len(signals) == 1


def test_cluster_focus_still_drops_below_focus_notional_floor():
    """Even with focus, $5k fills get dropped (below focus floor)."""
    fills = [
        _fill("0x111", "ETH", "Open Long", notional=5_000, tid=1),
        _fill("0x222", "ETH", "Open Long", notional=5_000, tid=2),
    ]
    scores = {f.whale: _good_score(f.whale) for f in fills}
    cfg = CorrelationConfig(focus_coins={"ETH"})
    signals = detect_cluster(fills, scores, whitelist={"ETH"}, config=cfg)
    assert signals == []


# ---------- NEW_OPEN: looser winrate + bumped severity on focus ----------

def test_new_open_focus_coin_winrate_below_default_but_above_focus_threshold():
    """WR 0.50 fails default (0.55) but passes focus_new_open_min_winrate (0.50)."""
    fills = [_fill("0x111", "ETH", "Open Long", notional=150_000, tid=1)]
    scores = {"0x111": _good_score("0x111", win_rate=0.50)}
    cfg = CorrelationConfig(focus_coins={"ETH"})
    signals = detect_new_open(fills, scores, whitelist={"ETH"}, config=cfg)
    assert len(signals) == 1


def test_new_open_focus_severity_promoted_to_warn():
    fills = [_fill("0x111", "ETH", "Open Long", notional=150_000, tid=1)]
    scores = {"0x111": _good_score("0x111", win_rate=0.65)}
    cfg = CorrelationConfig(focus_coins={"ETH"})
    signals = detect_new_open(fills, scores, whitelist={"ETH"}, config=cfg)
    assert signals[0].severity == SEV_WARN


def test_new_open_non_focus_stays_info():
    fills = [_fill("0x111", "BTC", "Open Long", notional=150_000, tid=1)]
    scores = {"0x111": _good_score("0x111", win_rate=0.65)}
    cfg = CorrelationConfig(focus_coins={"ETH"})
    signals = detect_new_open(fills, scores, whitelist={"BTC"}, config=cfg)
    assert signals[0].severity == SEV_INFO


# ---------- FLIP: critical on focus coin ----------

def test_flip_focus_severity_promoted_to_critical():
    fills = [
        _fill("0x111", "ETH", "Close Long", notional=200_000, tid=1, minutes_ago=60),
        _fill("0x111", "ETH", "Open Short", notional=200_000, tid=2, minutes_ago=50),
    ]
    scores = {"0x111": _good_score("0x111")}
    cfg = CorrelationConfig(focus_coins={"ETH"})
    signals = detect_flip(fills, scores, whitelist={"ETH"}, config=cfg)
    assert signals[0].severity == SEV_CRITICAL


def test_flip_non_focus_stays_warn():
    fills = [
        _fill("0x111", "BTC", "Close Long", notional=200_000, tid=1, minutes_ago=60),
        _fill("0x111", "BTC", "Open Short", notional=200_000, tid=2, minutes_ago=50),
    ]
    scores = {"0x111": _good_score("0x111")}
    cfg = CorrelationConfig(focus_coins={"ETH"})
    signals = detect_flip(fills, scores, whitelist={"BTC"}, config=cfg)
    assert signals[0].severity == SEV_WARN


# ---------- focus must be on whitelist too ----------

def test_focus_coin_not_in_whitelist_does_nothing():
    """ETH in focus_coins but missing from whitelist → no signal.

    Defensive against misconfig: don't surface signals on coins user didn't
    even put in their whitelist. focus_coins augments behavior, doesn't bypass.
    """
    fills = [
        _fill("0x111", "ETH", "Open Long", tid=1),
        _fill("0x222", "ETH", "Open Long", tid=2),
    ]
    scores = {f.whale: _good_score(f.whale) for f in fills}
    cfg = CorrelationConfig(focus_coins={"ETH"})
    signals = detect_cluster(fills, scores, whitelist={"BTC"}, config=cfg)
    assert signals == []


# ---------- detect_all: focus signals are routed to instant ----------

def test_detect_all_focus_signals_keep_critical_severity_for_instant_routing():
    """A 2-whale ETH cluster should produce a SEV_CRITICAL signal so
    split_by_mode routes it to instant Telegram delivery."""
    fills = [
        _fill("0x111", "ETH", "Open Long", tid=1),
        _fill("0x222", "ETH", "Open Long", tid=2),
    ]
    scores = {f.whale: _good_score(f.whale) for f in fills}
    cfg = CorrelationConfig(focus_coins={"ETH"})
    signals = detect_all(
        fills=fills, scores=scores, user_positions=[],
        whitelist={"ETH"}, config=cfg,
    )
    cluster_sigs = [s for s in signals if s.rule == SIG_CLUSTER]
    assert len(cluster_sigs) == 1
    assert cluster_sigs[0].severity == SEV_CRITICAL


def test_empty_focus_coins_means_no_special_treatment():
    """Default CorrelationConfig() (no focus_coins) -> behaves as Phase 2."""
    fills = [
        _fill("0x111", "ETH", "Open Long", tid=1),
        _fill("0x222", "ETH", "Open Long", tid=2),
    ]
    scores = {f.whale: _good_score(f.whale) for f in fills}
    cfg = CorrelationConfig()  # default: focus_coins is empty
    signals = detect_cluster(fills, scores, whitelist={"ETH"}, config=cfg)
    assert signals == []  # still needs 3 whales without focus
