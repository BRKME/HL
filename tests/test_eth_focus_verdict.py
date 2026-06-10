"""Tests for _compute_verdict — new methodology after analyst review.

Logic:
- trend_score: pure direction from EMA structure (-2..+2)
- exhaustion: oversold/overheated flags (RSI/funding/swing)
- exhaustion against trend DOWNGRADES verdict to WAIT
- exhaustion at reversal phases (CAPITULATION/EUPHORIA) flips to contrarian
- whale signals have ZERO weight (pending journal validation)
- regime BEAR/BULL blocks counter-trend entries (unless phase is reversal)
"""
from src.eth_focus import _compute_verdict, compute_verdict_pair


def test_no_data_returns_wait():
    v, _ = _compute_verdict(
        ta=None, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0,
        regime=None, phase=None,
    )
    assert v == "WAIT"


def test_clear_uptrend_returns_long():
    """EMA-aligned, no exhaustion → LONG."""
    ta = {"above_ema50": True, "above_ema200": True,
          "rsi_d1": 55, "last": 2000, "swing_low": 1500, "swing_high": 2200}
    v, r = _compute_verdict(ta=ta, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0, regime=None, phase=None)
    assert v == "LONG"
    assert "тренд вверх" in r.lower()


def test_clear_downtrend_returns_short():
    ta = {"above_ema50": False, "above_ema200": False,
          "rsi_d1": 50, "last": 2000, "swing_low": 1900, "swing_high": 2500}
    v, r = _compute_verdict(ta=ta, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0, regime=None, phase=None)
    assert v == "SHORT"


def test_weak_trend_partial_alignment_returns_wait():
    """Bounce in downtrend (above EMA200, below EMA50) → weak signal → WAIT."""
    ta = {"above_ema50": False, "above_ema200": True, "rsi_d1": 50}
    v, _ = _compute_verdict(ta=ta, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0, regime=None, phase=None)
    assert v == "WAIT"


def test_uptrend_with_overheated_downgrades_to_wait():
    """Uptrend + RSI 75 → WAIT (don't chase)."""
    ta = {"above_ema50": True, "above_ema200": True, "rsi_d1": 75}
    v, r = _compute_verdict(ta=ta, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0, regime=None, phase=None)
    assert v == "WAIT"
    assert "overbought" in r.lower() or "rsi" in r.lower()


def test_downtrend_with_oversold_downgrades_to_wait():
    ta = {"above_ema50": False, "above_ema200": False,
          "rsi_d1": 25, "last": 2000, "swing_low": 1950, "swing_high": 2500}
    v, r = _compute_verdict(ta=ta, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0, regime=None, phase=None)
    assert v == "WAIT"
    assert "oversold" in r.lower() or "rsi" in r.lower()


def test_extreme_funding_creates_exhaustion():
    """Funding +18% even without RSI extreme → overheated → WAIT."""
    ta = {"above_ema50": True, "above_ema200": True, "rsi_d1": 60}
    v, _ = _compute_verdict(ta=ta, funding_apr_pct=18,
        whale_net_long=None, whale_cluster_count=0, regime=None, phase=None)
    assert v == "WAIT"


def test_uptrend_no_exhaustion_stays_long():
    ta = {"above_ema50": True, "above_ema200": True,
          "rsi_d1": 55, "last": 2000, "swing_low": 1500, "swing_high": 2500}
    v, _ = _compute_verdict(ta=ta, funding_apr_pct=3,
        whale_net_long=None, whale_cluster_count=0, regime=None, phase=None)
    assert v == "LONG"


def test_long_blocked_by_bear_regime():
    ta = {"above_ema50": True, "above_ema200": True, "rsi_d1": 55}
    v, r = _compute_verdict(ta=ta, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0,
        regime="BEAR", phase=None)
    assert v == "WAIT"
    assert "BEAR" in r


def test_short_blocked_by_bull_regime():
    ta = {"above_ema50": False, "above_ema200": False, "rsi_d1": 50}
    v, r = _compute_verdict(ta=ta, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0,
        regime="BULL", phase=None)
    assert v == "WAIT"
    assert "BULL" in r


def test_capitulation_with_oversold_returns_long():
    """CAPITULATION + oversold → LONG, even against trend."""
    ta = {"above_ema50": False, "above_ema200": False,
          "rsi_d1": 25, "last": 1700, "swing_low": 1690, "swing_high": 2500}
    v, r = _compute_verdict(ta=ta, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0,
        regime="BEAR", phase="CAPITULATION")
    assert v == "LONG"
    assert "capitulation" in r.lower() or "дно" in r.lower()


def test_capitulation_without_oversold_no_long():
    """CAPITULATION but no exhaustion → don't force LONG."""
    ta = {"above_ema50": False, "above_ema200": False,
          "rsi_d1": 50, "last": 2000, "swing_low": 1500, "swing_high": 2500}
    v, _ = _compute_verdict(ta=ta, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0,
        regime="BEAR", phase="CAPITULATION")
    assert v != "LONG"


def test_euphoria_with_overheated_in_bull_is_wait():
    """Инвариант иерархии (политика оператора, 2026-06-10): стратегия BULL →
    тактика никогда не SHORT. Раньше здесь ожидался SHORT — контрарианский
    разворот у вершины; теперь при BULL это WAIT с фиксацией прибыли, а SHORT
    остаётся доступен только когда режим уже не бычий (см.
    tests/test_strategy_hierarchy.py)."""
    ta = {"above_ema50": True, "above_ema200": True, "rsi_d1": 78}
    v, r = _compute_verdict(ta=ta, funding_apr_pct=20,
        whale_net_long=None, whale_cluster_count=0,
        regime="BULL", phase="EUPHORIA")
    assert v == "WAIT"
    assert "фиксируй" in r.lower() or "иерарх" in r.lower()


def test_late_bear_with_oversold_returns_long():
    ta = {"above_ema50": False, "above_ema200": False,
          "rsi_d1": 28, "last": 1700, "swing_low": 1690, "swing_high": 2500}
    v, _ = _compute_verdict(ta=ta, funding_apr_pct=-12,
        whale_net_long=None, whale_cluster_count=0,
        regime="BEAR", phase="LATE_BEAR")
    assert v == "LONG"


def test_whale_cluster_does_not_change_verdict():
    """Whale weight = 0 — verdict same with or without whale signal."""
    ta = {"above_ema50": True, "above_ema200": True, "rsi_d1": 55}
    v_no, _ = _compute_verdict(ta=ta, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0, regime=None, phase=None)
    v_with, _ = _compute_verdict(ta=ta, funding_apr_pct=None,
        whale_net_long=False, whale_cluster_count=10,  # strong short whale
        regime=None, phase=None)
    assert v_no == v_with  # whales must not flip


def test_compute_verdict_pair_returns_raw_and_final():
    """Pair returns (raw=no-regime, final=with-regime)."""
    ta = {"above_ema50": True, "above_ema200": True, "rsi_d1": 55}
    (raw_v, _), (final_v, _) = compute_verdict_pair(
        ta=ta, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0,
        regime="BEAR", phase=None)
    assert raw_v == "LONG"
    assert final_v == "WAIT"


def test_raw_and_final_identical_when_no_regime():
    ta = {"above_ema50": True, "above_ema200": True, "rsi_d1": 55}
    (raw_v, _), (final_v, _) = compute_verdict_pair(
        ta=ta, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0,
        regime=None, phase=None)
    assert raw_v == final_v == "LONG"


def test_raw_wait_final_long_at_capitulation():
    """Bottom phase override: raw says WAIT (counter-trend exhaustion),
    final says LONG (CAPITULATION + oversold = reversal entry)."""
    ta = {"above_ema50": False, "above_ema200": False,
          "rsi_d1": 25, "last": 1700, "swing_low": 1690, "swing_high": 2500}
    (raw_v, _), (final_v, _) = compute_verdict_pair(
        ta=ta, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0,
        regime="BEAR", phase="CAPITULATION")
    assert raw_v == "WAIT"
    assert final_v == "LONG"


def test_30_may_scenario_returns_wait():
    """User's 30 May report: downtrend + RSI 32 + at support + BEAR.
    Counter-trend exhaustion → WAIT."""
    ta = {"above_ema50": False, "above_ema200": False, "rsi_d1": 32,
          "last": 2015, "swing_low": 2009, "swing_high": 2500}
    v, _ = _compute_verdict(ta=ta, funding_apr_pct=2.1,
        whale_net_long=None, whale_cluster_count=0,
        regime="BEAR", phase="EARLY_BEAR")
    assert v == "WAIT"


def test_strong_uptrend_at_resistance_downgrades():
    """Bull near swing high → WAIT for breakout."""
    ta = {"above_ema50": True, "above_ema200": True,
          "rsi_d1": 65, "last": 2480, "swing_low": 1700, "swing_high": 2500}
    v, _ = _compute_verdict(ta=ta, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0, regime=None, phase=None)
    assert v == "WAIT"
