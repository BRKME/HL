"""Tests for _compute_verdict — Variant B short-form ETH Focus."""
from src.eth_focus import _compute_verdict


# ---------- WAIT cases ----------

def test_verdict_wait_when_no_data():
    v, r = _compute_verdict(
        ta=None, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0,
        regime=None, phase=None,
    )
    assert v == "WAIT"


def test_verdict_wait_when_signals_balanced():
    """1 vs 1 — not decisive enough to act."""
    ta = {"above_ema50": True, "above_ema200": True,  # +2 long
          "rsi_d1": 75,  # +1 short
          "last": 2000, "swing_high": 2050, "swing_low": 1700}  # +1 short (resistance)
    # → long 2, short 2 — WAIT
    v, r = _compute_verdict(
        ta=ta, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0,
        regime=None, phase=None,
    )
    assert v == "WAIT"
    assert "смешан" in r.lower() or "сигнал" in r.lower()


# ---------- LONG cases ----------

def test_verdict_long_when_strong_long_signals():
    """All long signals aligned, no blocker."""
    ta = {
        "above_ema50": True, "above_ema200": True,  # +2 long
        "rsi_d1": 25,  # +1 long (oversold)
        "last": 2000, "swing_low": 1990, "swing_high": 2500,  # +1 long (at support)
    }
    v, r = _compute_verdict(
        ta=ta, funding_apr_pct=-12,  # +2 long (cheap short funding)
        whale_net_long=None, whale_cluster_count=0,
        regime=None, phase=None,
    )
    assert v == "LONG"
    assert "long" in r.lower()


def test_verdict_long_blocked_by_bear_regime():
    """All long signals BUT BEAR regime → WAIT (don't fight the trend)."""
    ta = {
        "above_ema50": True, "above_ema200": True,
        "rsi_d1": 25,
        "last": 2000, "swing_low": 1990, "swing_high": 2500,
    }
    v, r = _compute_verdict(
        ta=ta, funding_apr_pct=-12,
        whale_net_long=None, whale_cluster_count=0,
        regime="BEAR", phase=None,
    )
    assert v == "WAIT"
    assert "BEAR" in r


def test_verdict_long_blocked_by_early_bear_phase():
    """Phase EARLY_BEAR alone blocks long too."""
    ta = {
        "above_ema50": True, "above_ema200": True,
        "rsi_d1": 25,
        "last": 2000, "swing_low": 1990, "swing_high": 2500,
    }
    v, r = _compute_verdict(
        ta=ta, funding_apr_pct=-12,
        whale_net_long=None, whale_cluster_count=0,
        regime=None, phase="EARLY_BEAR",
    )
    assert v == "WAIT"


# ---------- SHORT cases ----------

def test_verdict_short_when_strong_short_signals():
    """All short signals aligned, no blocker."""
    ta = {
        "above_ema50": False, "above_ema200": False,  # +2 short
        "rsi_d1": 75,  # +1 short
        "last": 2000, "swing_low": 1500, "swing_high": 2010,  # +1 short (resistance)
    }
    v, r = _compute_verdict(
        ta=ta, funding_apr_pct=18,  # +2 short
        whale_net_long=None, whale_cluster_count=0,
        regime=None, phase=None,
    )
    assert v == "SHORT"
    assert "short" in r.lower()


def test_verdict_short_blocked_by_bull_regime():
    """All short signals BUT BULL regime → WAIT."""
    ta = {
        "above_ema50": False, "above_ema200": False,
        "rsi_d1": 75,
    }
    v, r = _compute_verdict(
        ta=ta, funding_apr_pct=18,
        whale_net_long=None, whale_cluster_count=0,
        regime="BULL", phase=None,
    )
    assert v == "WAIT"
    assert "BULL" in r


# ---------- whale signals ----------

def test_verdict_uses_whale_cluster_when_count_high():
    """Whale activity tips a borderline call."""
    ta = {
        "above_ema50": False, "above_ema200": False,  # +2 short
        "rsi_d1": 50,
    }
    # 2 cluster events, net long → +1 long. Long 1, Short 2 → margin 1 → WAIT
    v, _ = _compute_verdict(
        ta=ta, funding_apr_pct=None,
        whale_net_long=True, whale_cluster_count=2,
        regime=None,
    )
    assert v == "WAIT"

    # Without whales contributing: Long 0, Short 2 — margin 2 → SHORT
    v2, _ = _compute_verdict(
        ta=ta, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0,
        regime=None,
    )
    assert v2 == "SHORT"


# ---------- rationale wording ----------

def test_long_rationale_mentions_reasons():
    ta = {
        "above_ema50": True, "above_ema200": True,
        "rsi_d1": 25,
        "last": 2000, "swing_low": 1990, "swing_high": 2500,
    }
    _, r = _compute_verdict(
        ta=ta, funding_apr_pct=None,
        whale_net_long=None, whale_cluster_count=0,
        regime=None,
    )
    assert "тренд" in r.lower() or "rsi" in r.lower() or "поддерж" in r.lower()


def test_short_rationale_mentions_reasons():
    ta = {
        "above_ema50": False, "above_ema200": False,
        "rsi_d1": 75,
    }
    _, r = _compute_verdict(
        ta=ta, funding_apr_pct=18,
        whale_net_long=None, whale_cluster_count=0,
        regime=None,
    )
    # rationale references at least one short reason
    assert ("тренд" in r.lower() or "rsi" in r.lower()
            or "funding" in r.lower())


# ---------- the actual report from 30 May ----------

def test_actual_30_may_report_returns_wait():
    """User's actual data 30 May (the one that confused them):
    Trend down, RSI 32, at swing low, funding +2.1%, BEAR regime.

    Expected: WAIT — long bias (oversold + support) but BEAR regime blocks.
    """
    ta = {
        "above_ema50": False, "above_ema200": False,  # +2 short
        "rsi_d1": 32,  # not below 30, no bonus
        "last": 2015, "swing_low": 2009, "swing_high": 2500,  # +1 long (at support)
    }
    v, r = _compute_verdict(
        ta=ta, funding_apr_pct=2.1,  # neutral, no contribution
        whale_net_long=None, whale_cluster_count=0,
        regime="BEAR", phase="EARLY_BEAR",
    )
    # Trend says short (+2), support says long (+1) → margin 1 → WAIT regardless
    assert v == "WAIT"
