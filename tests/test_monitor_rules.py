"""Tests for src/monitor_rules.py and src/oracai_history.py."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.decisions_log import Decision
from src.matcher import MatchResult
from src.monitor_rules import (
    Alert,
    RuleConfig,
    rule_sl_approach,
    rule_time_stop,
    rule_regime_flip_since_entry,
    rule_regime_flip_daily,
    rule_profit_trail,
    rule_liquidation_close,
    evaluate_all,
    SEV_INFO,
    SEV_WARN,
    SEV_CRITICAL,
)
from src.portfolio import AggregatedPerpPosition
from src.oracai_history import regime_changed, phase_changed


NOW = datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc)


# ---------- helpers ----------

def _pos(coin="BTC", net_size=0.5, entry=80000.0, pnl=0.0, liq_dist_pct=25.0):
    return AggregatedPerpPosition(
        coin=coin,
        net_size=net_size,
        weighted_entry=entry,
        total_pnl=pnl,
        contributors=[("main", net_size)],
        avg_leverage=10.0,
        max_liquidation_distance_pct=liq_dist_pct,
    )


def _dec(
    coin="BTC", entry=80000.0, alloc=200.0, sl=76000.0,
    ts_days_ago=3, regime="BULL", phase="MID_BULL",
):
    return Decision(
        ts=NOW - timedelta(days=ts_days_ago),
        signal="MODERATE",
        coin=coin,
        entry=entry,
        alloc_usd=alloc,
        expected_size=alloc / entry,
        sl_price=sl,
        sl_pct=-5.0,
        sl_method="atr",
        atr14=2000.0,
        regime_at_entry=regime,
        phase_at_entry=phase,
    )


def _tracked(pos=None, dec=None, days=3):
    return MatchResult(
        position=pos or _pos(),
        decision=dec or _dec(),
        status="tracked",
        days_in_position=days,
    )


def _orphan(pos=None):
    return MatchResult(position=pos or _pos(), decision=None, status="orphan")


# ---------- SL_APPROACH ----------

def test_sl_approach_silent_when_far():
    """Mark $82,000 vs SL $76,000 = 7.3% distance, no alert at default 3% threshold."""
    alerts = rule_sl_approach(_tracked(), current_mark=82000.0, warning_pct=3.0)
    assert alerts == []


def test_sl_approach_warns_when_within_threshold():
    """Mark $77,500 vs SL $76,000 = 1.9% — within 3% threshold."""
    alerts = rule_sl_approach(_tracked(), current_mark=77500.0, warning_pct=3.0)
    assert len(alerts) == 1
    assert alerts[0].rule == "SL_APPROACH"
    assert alerts[0].severity == SEV_WARN


def test_sl_approach_critical_when_below_sl():
    """Mark $75,000 < SL $76,000 — already past SL."""
    alerts = rule_sl_approach(_tracked(), current_mark=75000.0)
    assert len(alerts) == 1
    assert alerts[0].severity == SEV_CRITICAL


def test_sl_approach_orphan_skipped():
    """Orphan has no recommended SL — we don't second-guess user's manual SL."""
    alerts = rule_sl_approach(_orphan(), current_mark=77500.0)
    assert alerts == []


def test_sl_approach_handles_zero_sl():
    """sl_price=0 means no SL recorded — silent."""
    dec = _dec(sl=0.0)
    alerts = rule_sl_approach(_tracked(dec=dec), current_mark=80000.0)
    assert alerts == []


def test_sl_approach_for_short_position():
    """Short: SL above mark. Mark $80,000, SL $82,000 = 2.5% distance."""
    pos = _pos(net_size=-0.5)
    dec = _dec(sl=82000.0)
    alerts = rule_sl_approach(_tracked(pos=pos, dec=dec), current_mark=80000.0)
    assert len(alerts) == 1
    assert alerts[0].severity == SEV_WARN


# ---------- TIME_STOP ----------

def test_time_stop_silent_under_limit():
    alerts = rule_time_stop(_tracked(days=5), max_days=7)
    assert alerts == []


def test_time_stop_warns_at_limit():
    alerts = rule_time_stop(_tracked(days=7), max_days=7)
    assert len(alerts) == 1
    assert alerts[0].rule == "TIME_STOP"
    assert alerts[0].details["days"] == 7


def test_time_stop_warns_past_limit():
    alerts = rule_time_stop(_tracked(days=12), max_days=7)
    assert len(alerts) == 1
    assert alerts[0].details["days"] == 12


def test_time_stop_skipped_for_orphan():
    alerts = rule_time_stop(_orphan(), max_days=7)
    assert alerts == []


# ---------- REGIME_FLIP_SINCE_ENTRY ----------

def test_regime_flip_since_entry_warns_when_changed():
    alerts = rule_regime_flip_since_entry(_tracked(), current_regime="BEAR")
    assert len(alerts) == 1
    assert alerts[0].rule == "REGIME_FLIP_SINCE_ENTRY"
    assert "BULL" in alerts[0].message and "BEAR" in alerts[0].message


def test_regime_flip_since_entry_silent_when_same():
    alerts = rule_regime_flip_since_entry(_tracked(), current_regime="BULL")
    assert alerts == []


def test_regime_flip_since_entry_skipped_for_orphan():
    alerts = rule_regime_flip_since_entry(_orphan(), current_regime="BEAR")
    assert alerts == []


def test_regime_flip_since_entry_skipped_without_entry_regime():
    """Old decisions without regime_at_entry — no alert (insufficient data)."""
    dec = _dec(regime=None)
    alerts = rule_regime_flip_since_entry(_tracked(dec=dec), current_regime="BEAR")
    assert alerts == []


def test_regime_flip_since_entry_skipped_without_current_regime():
    alerts = rule_regime_flip_since_entry(_tracked(), current_regime=None)
    assert alerts == []


# ---------- PROFIT_TRAIL ----------

def test_profit_trail_silent_below_threshold():
    """0.5 BTC entry $80k = $40k notional, $2k PnL = 5% — below 10%."""
    pos = _pos(net_size=0.5, entry=80000.0, pnl=2000.0)
    alerts = rule_profit_trail(_tracked(pos=pos), threshold_pct=10.0)
    assert alerts == []


def test_profit_trail_alerts_above_threshold():
    """0.5 BTC entry $80k = $40k notional, $4.5k PnL = 11.25%."""
    pos = _pos(net_size=0.5, entry=80000.0, pnl=4500.0)
    alerts = rule_profit_trail(_tracked(pos=pos), threshold_pct=10.0)
    assert len(alerts) == 1
    assert alerts[0].rule == "PROFIT_TRAIL"
    assert alerts[0].severity == SEV_INFO


def test_profit_trail_works_for_orphan():
    """Orphan with big profit should still surface — user may want to trail."""
    pos = _pos(net_size=0.5, entry=80000.0, pnl=5000.0)
    alerts = rule_profit_trail(_orphan(pos=pos))
    assert len(alerts) == 1


# ---------- LIQUIDATION_CLOSE ----------

def test_liquidation_close_silent_when_far():
    pos = _pos(liq_dist_pct=30.0)
    alerts = rule_liquidation_close(_tracked(pos=pos), critical_pct=15.0)
    assert alerts == []


def test_liquidation_close_critical_when_near():
    pos = _pos(liq_dist_pct=8.0)
    alerts = rule_liquidation_close(_tracked(pos=pos), critical_pct=15.0)
    assert len(alerts) == 1
    assert alerts[0].severity == SEV_CRITICAL


def test_liquidation_close_skipped_when_distance_zero():
    """Distance 0 = spot-like or no data — don't alert spuriously."""
    pos = _pos(liq_dist_pct=0.0)
    alerts = rule_liquidation_close(_tracked(pos=pos))
    assert alerts == []


# ---------- REGIME_FLIP_DAILY (portfolio-wide) ----------

def test_regime_flip_daily_alerts_on_regime_change():
    yesterday = {"regime": "BULL", "cycle": {"phase": "MID_BULL"}}
    today = {"regime": "BEAR", "cycle": {"phase": "MID_BULL"}}
    alerts = rule_regime_flip_daily(yesterday, today)
    assert len(alerts) == 1
    assert alerts[0].rule == "REGIME_FLIP_DAILY"
    assert alerts[0].coin == "*"


def test_regime_flip_daily_alerts_on_phase_change():
    yesterday = {"regime": "BULL", "cycle": {"phase": "MID_BULL"}}
    today = {"regime": "BULL", "cycle": {"phase": "LATE_BULL"}}
    alerts = rule_regime_flip_daily(yesterday, today)
    assert len(alerts) == 1
    assert alerts[0].rule == "PHASE_FLIP_DAILY"
    assert alerts[0].severity == SEV_INFO


def test_regime_flip_daily_alerts_both_when_both_change():
    yesterday = {"regime": "BULL", "cycle": {"phase": "MID_BULL"}}
    today = {"regime": "BEAR", "cycle": {"phase": "EARLY_BEAR"}}
    alerts = rule_regime_flip_daily(yesterday, today)
    assert len(alerts) == 2


def test_regime_flip_daily_silent_when_unchanged():
    snap = {"regime": "BULL", "cycle": {"phase": "MID_BULL"}}
    alerts = rule_regime_flip_daily(snap, snap)
    assert alerts == []


def test_regime_flip_daily_silent_when_missing_yesterday():
    today = {"regime": "BULL", "cycle": {"phase": "MID_BULL"}}
    alerts = rule_regime_flip_daily(None, today)
    assert alerts == []


# ---------- regime_changed / phase_changed primitives ----------

def test_regime_changed_returns_tuple():
    assert regime_changed({"regime": "BULL"}, {"regime": "BEAR"}) == ("BULL", "BEAR")


def test_regime_changed_returns_none_when_same():
    assert regime_changed({"regime": "BULL"}, {"regime": "BULL"}) is None


def test_regime_changed_returns_none_when_missing_field():
    assert regime_changed({}, {"regime": "BULL"}) is None
    assert regime_changed({"regime": "BULL"}, {}) is None


def test_phase_changed_handles_nested_cycle():
    prev = {"cycle": {"phase": "MID_BULL"}}
    curr = {"cycle": {"phase": "LATE_BULL"}}
    assert phase_changed(prev, curr) == ("MID_BULL", "LATE_BULL")


def test_phase_changed_returns_none_without_cycle():
    assert phase_changed({}, {"cycle": {"phase": "BULL"}}) is None


# ---------- evaluate_all coordinator ----------

def test_evaluate_all_aggregates_and_sorts():
    """Multiple rules fire; output sorted critical > warn > info."""
    pos_near_liq = _pos(coin="BTC", net_size=0.5, entry=80000, liq_dist_pct=8.0)
    pos_profit = _pos(coin="ETH", net_size=10, entry=3000, pnl=4000, liq_dist_pct=50.0)

    matches = [
        _tracked(pos=pos_near_liq, dec=_dec(coin="BTC"), days=5),
        _tracked(pos=pos_profit, dec=_dec(coin="ETH", entry=3000), days=4),
    ]
    marks = {"BTC": 80000.0, "ETH": 3300.0}
    today = {"regime": "BULL", "cycle": {"phase": "MID_BULL"}}
    yesterday = {"regime": "BEAR", "cycle": {"phase": "MID_BULL"}}

    alerts = evaluate_all(matches, marks, today, yesterday)

    rules_seen = [a.rule for a in alerts]
    assert "LIQUIDATION_CLOSE" in rules_seen
    assert "REGIME_FLIP_DAILY" in rules_seen
    assert "PROFIT_TRAIL" in rules_seen

    # critical alerts come first
    severities = [a.severity for a in alerts]
    assert severities == sorted(severities, reverse=True)


def test_evaluate_all_with_no_matches_only_returns_portfolio_alerts():
    today = {"regime": "BEAR"}
    yesterday = {"regime": "BULL"}
    alerts = evaluate_all([], {}, today, yesterday)
    assert len(alerts) == 1
    assert alerts[0].rule == "REGIME_FLIP_DAILY"


def test_evaluate_all_handles_missing_snapshots():
    """No OracAI data anywhere — engine still runs, just no regime alerts."""
    matches = [_tracked()]
    marks = {"BTC": 80000.0}
    alerts = evaluate_all(matches, marks, None, None)
    # Some position-level rules may still fire, but no regime ones
    rules_seen = {a.rule for a in alerts}
    assert "REGIME_FLIP_DAILY" not in rules_seen
    assert "REGIME_FLIP_SINCE_ENTRY" not in rules_seen


def test_evaluate_all_uses_custom_config():
    """Stricter time_stop_days exposes a position that wouldn't trigger by default."""
    pos = _pos(coin="BTC", liq_dist_pct=50.0)
    matches = [_tracked(pos=pos, days=4)]
    alerts = evaluate_all(matches, {"BTC": 82000.0}, None, None,
                          config=RuleConfig(time_stop_days=3))
    assert any(a.rule == "TIME_STOP" for a in alerts)


# ---------- oracai_history fetch (mocked) ----------

def test_fetch_snapshot_days_ago_returns_none_when_no_commit():
    """Empty commits list -> None, no crash."""
    from src.oracai_history import fetch_snapshot_days_ago
    with patch("src.oracai_history._gh_request") as mock_gh:
        mock_gh.return_value = []
        result = fetch_snapshot_days_ago(1, now=NOW)
        assert result is None


def test_fetch_snapshot_days_ago_returns_parsed_json():
    from src.oracai_history import fetch_snapshot_days_ago
    expected = {"regime": "BULL", "cycle": {"phase": "MID_BULL"}}
    with patch("src.oracai_history._gh_request") as mock_gh, \
         patch("src.oracai_history.requests.get") as mock_get:
        mock_gh.return_value = [{"sha": "abc123"}]
        mock_get.return_value.json.return_value = expected
        mock_get.return_value.raise_for_status = lambda: None
        result = fetch_snapshot_days_ago(1, now=NOW)
        assert result == expected
        # raw URL contains the SHA
        called_url = mock_get.call_args.args[0]
        assert "abc123" in called_url
