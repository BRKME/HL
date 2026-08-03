"""Characterisation tests for src/oracai.py — the regime layer.

This module reached August 2026 with no tests at all, which matters more
than the usual coverage complaint: the end-of-August analysis compares
WR(verdict) against WR(verdict_raw) precisely to decide whether this layer
earns its place. Measuring an unpinned decision ladder would tell us about
whatever it happens to do that week, not about the design.

These tests pin the ladder as it stands rather than asserting it is right.
The order matters: EXIT beats SKIP, regime beats cycle, and the whole thing
is deliberately biased toward entry because a missed week of DCA is
irreversible while a missed exit is caught by the stop. If a branch here
changes, that is a decision to make on purpose.
"""
import pytest

from src.oracai import derive_signal_strength


def snap(regime="BULL", risk_state="RISK_ON", action="HOLD",
         phase="MID_BULL", top=0.2, bottom=0.5, conf=0.8):
    return {
        "regime": regime,
        "confidence": {"quality_adjusted": conf},
        "risk": {"risk_state": risk_state},
        "cycle": {"action": action, "phase": phase,
                  "top_proximity": top, "bottom_proximity": bottom},
    }


def sig(**kw):
    return derive_signal_strength(snap(**kw))["signal"]


# ------------------------------------------------------------ 1. EXIT

@pytest.mark.parametrize("state", ["CRISIS", "TAIL"])
def test_systemic_risk_exits_regardless_of_bull(state):
    """Systemic risk outranks everything, including a perfect bull setup."""
    assert sig(regime="BULL", risk_state=state, action="STRONG_BUY") == "EXIT"


@pytest.mark.parametrize("regime", ["BEAR", "TRANS"])
def test_bearish_regime_with_risk_off_exits(regime):
    assert sig(regime=regime, risk_state="RISK_OFF") == "EXIT"


@pytest.mark.parametrize("action", ["SELL", "STRONG_SELL", "ПРОДАВАТЬ"])
def test_bearish_regime_with_sell_action_exits(action):
    assert sig(regime="BEAR", action=action) == "EXIT"


def test_exit_carries_zero_leverage():
    assert derive_signal_strength(snap(risk_state="CRISIS"))["leverage"] == 0


# ------------------------------------------------------------ 2-4. SKIP

@pytest.mark.parametrize("regime", ["BEAR", "TRANS"])
def test_bearish_regime_alone_skips(regime):
    """Regime outranks cycle: bull cycle doesn't rescue a bear regime."""
    assert sig(regime=regime, action="STRONG_BUY", phase="MID_BULL") == "SKIP"


def test_near_top_skips_even_in_bull():
    assert sig(top=0.70) == "SKIP"


def test_just_below_top_threshold_does_not_skip():
    assert sig(top=0.69) != "SKIP"


def test_low_confidence_skips():
    assert sig(conf=0.29) == "SKIP"


def test_confidence_at_threshold_passes():
    assert sig(conf=0.30) != "SKIP"


# ------------------------------------------------------- 5. DEFENSIVE

def test_bull_regime_with_bear_phase_is_defensive():
    out = derive_signal_strength(snap(regime="BULL", phase="EARLY_BEAR"))
    assert out["signal"] == "MODERATE"
    assert out["leverage"] == 1
    assert out["raw"]["conflict"] is True


def test_defensive_is_flagged_in_raw():
    out = derive_signal_strength(snap(regime="BULL", phase="MID_BEAR"))
    assert out["raw"].get("defensive") is True


def test_conflict_detected_both_directions():
    """A bear regime with a bull phase is also a conflict — but skips."""
    out = derive_signal_strength(snap(regime="BEAR", phase="MID_BULL"))
    assert out["raw"]["conflict"] is True
    assert out["signal"] == "SKIP"


# ---------------------------------------------------------- 6. STRONG

def test_full_bullish_alignment_is_strong():
    out = derive_signal_strength(snap(
        regime="BULL", action="STRONG_BUY", risk_state="RISK_ON",
        phase="MID_BULL", top=0.2, bottom=0.5))
    assert out["signal"] == "STRONG"
    assert out["leverage"] == 2


def test_strong_requires_distance_from_bottom():
    """bottom_proximity below 0.30 downgrades STRONG to MODERATE."""
    assert sig(action="STRONG_BUY", bottom=0.29) == "MODERATE"


def test_strong_requires_buy_action():
    assert sig(action="HOLD", bottom=0.5) == "MODERATE"


@pytest.mark.parametrize("action", ["BUY", "ACCUMULATE", "ПОКУПАТЬ", "ДОКУПИТЬ"])
def test_all_buy_synonyms_reach_strong(action):
    assert sig(action=action) == "STRONG"


# -------------------------------------------------------- 7-8. MODERATE

def test_plain_bull_is_moderate():
    assert sig(action="HOLD") == "MODERATE"


def test_elevated_risk_still_allows_moderate():
    assert sig(risk_state="ELEVATED", action="HOLD") == "MODERATE"


def test_unknown_risk_state_falls_through_to_skip():
    assert sig(risk_state="WEIRD", action="HOLD") == "SKIP"


# ------------------------------------------------------------- robustness

def test_empty_snapshot_does_not_crash():
    out = derive_signal_strength({})
    assert out["signal"] == "SKIP"
    assert out["leverage"] == 0


def test_missing_cycle_block_is_tolerated():
    assert derive_signal_strength({"regime": "BULL"})["signal"] == "SKIP"


def test_regime_case_is_normalised():
    assert sig(regime="bull", action="STRONG_BUY") == "STRONG"


def test_reasons_are_always_populated():
    for kw in ({}, {"risk_state": "CRISIS"}, {"regime": "BEAR"}, {"top": 0.9}):
        assert derive_signal_strength(snap(**kw))["reasons"]
