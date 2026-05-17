"""Tests for report polish fixes (17 May 2026 prod feedback):
1. Alerts sorted by SL distance within same severity
2. All-time ROI shown via fallback when start_value missing
3. SL risk shown when liq distance unavailable
"""
from datetime import datetime, timezone

import pytest

from src.daily_report import render_daily_report
from src.matcher import MatchResult
from src.monitor_rules import Alert, evaluate_all, SEV_WARN, SEV_CRITICAL
from src.portfolio import AggregatedPerpPosition
from src.portfolio_performance import PerformanceSnapshot, PeriodStats
from src.sl_visibility import SLOrder


NOW = datetime(2026, 5, 17, 0, 0, tzinfo=timezone.utc)


def _pos(coin, net_size, entry, liq_dist=0.0):
    return AggregatedPerpPosition(
        coin=coin, net_size=net_size, weighted_entry=entry, total_pnl=0,
        contributors=[("main", net_size)], avg_leverage=5,
        max_liquidation_distance_pct=liq_dist,
    )


def _orphan(pos):
    return MatchResult(position=pos, decision=None, status="orphan")


def _sl(coin, trigger, side="long"):
    return SLOrder(coin=coin, trigger_px=trigger, size=0, protects_side=side,
                   order_type="Stop Market", oid=1, account="main",
                   is_position_attached=True)


# ---------- Fix 1: alerts sorted by distance within same severity ----------

def test_alerts_sorted_by_sl_distance_within_warn_severity():
    """When two tight SLs both fire, tighter ATR-distance comes first."""
    eth = _pos("ETH", 0.7238, 2173.0, liq_dist=89.4)
    btc = _pos("BTC", 0.00779, 78101.0, liq_dist=0.0)
    # Make both tight by ATR: ETH 60/200 = 0.3×, BTC 860/2000 = 0.43×
    # Tighter = lower ATR mult = BTC at 0.43, ETH at 0.3 — wait, ETH tighter
    # Actually: distance_pct ascending = ETH first (it's tighter)
    sls = [_sl("ETH", 2122.0), _sl("BTC", 77319.0)]
    alerts = evaluate_all(
        matches=[_orphan(eth), _orphan(btc)],
        marks={"ETH": 2180.90, "BTC": 78179.00},
        current_snapshot=None, yesterday_snapshot=None,
        sl_orders=sls,
        coin_atrs={"ETH": 200.0, "BTC": 2200.0},  # both tight
    )
    # Both should produce ORPHAN_SL_APPROACH
    sl_alerts = [a for a in alerts if a.rule == "ORPHAN_SL_APPROACH"]
    assert len(sl_alerts) == 2
    # BTC distance_pct = 1.1%, ETH = 2.7% → BTC first (tighter percent)
    assert sl_alerts[0].coin == "BTC"
    assert sl_alerts[1].coin == "ETH"


def test_alerts_critical_still_above_warn_after_sort():
    """A critical alert with distance 5% still beats warn with distance 0.5%."""
    eth = _pos("ETH", 0.7238, 2173.0)
    btc = _pos("BTC", 0.00779, 78101.0)
    sls = [_sl("ETH", 2090.0), _sl("BTC", 78400.0)]  # ETH 4% safe, BTC mark BELOW SL = critical
    alerts = evaluate_all(
        matches=[_orphan(eth), _orphan(btc)],
        marks={"ETH": 2180.0, "BTC": 78200.0},  # BTC mark $78200 < SL $78400 → critical
        current_snapshot=None, yesterday_snapshot=None,
        sl_orders=sls,
    )
    # First alert is critical (BTC past SL)
    assert alerts[0].severity == SEV_CRITICAL


def test_alerts_without_distance_keep_their_position():
    """A regime-flip warn has no distance_pct — it shouldn't crash the sort."""
    pos = _pos("ETH", 0.7238, 2173.0)
    alerts = evaluate_all(
        matches=[_orphan(pos)],
        marks={"ETH": 2180.0},
        current_snapshot={"regime": "BEAR"},
        yesterday_snapshot={"regime": "BULL"},
        sl_orders=[_sl("ETH", 2090.0)],  # safe
    )
    # No crash, regime flip alert is present
    rules = {a.rule for a in alerts}
    assert "REGIME_FLIP_DAILY" in rules


# ---------- Fix 2: ROI fallback for all-time ----------

def _perf_alltime_missing_start(day_pnl=0, week_pnl=-19, month_pnl=-24,
                                  alltime_pnl=-3036, current_value=2336):
    """Simulate the real-world case: HL all-time start_value comes back 0
    or negative when account history is incomplete."""
    def mk(name, pnl, start, end):
        return PeriodStats(period=name, pnl=pnl, start_value=start,
                           end_value=end, vlm=0,
                           roi_pct=(pnl / start * 100) if start > 0 else 0)
    return PerformanceSnapshot(
        address="combined",
        day=mk("day", day_pnl, 2310, current_value),
        week=mk("week", week_pnl, 2355, current_value),
        month=mk("month", month_pnl, 2360, current_value),
        all_time=mk("allTime", alltime_pnl, 0.0, current_value),  # start=0!
        current_account_value=current_value,
    )


def test_alltime_row_removed_no_anchor_bias():
    """All-time row was removed in UI refinement round 2 (anchor bias).
    The fallback logic itself stays — applies to other periods that
    might have start_value=0."""
    perf = _perf_alltime_missing_start(alltime_pnl=-3036, current_value=2336)
    msgs = render_daily_report([], [], {}, None, 2336, NOW, performance=perf)
    text = "\n".join(msgs)
    # All-time not shown anywhere
    assert "All-time" not in text
    assert "-$3 036" not in text
    # Week/month still shown
    assert "Неделя" in text


def test_monthly_roi_falls_back_when_start_missing():
    """ROI fallback applies to month period too (general case).
    Implied start = end_value - pnl = 2000 - (-200) = 2200 → ROI = -9.1%."""
    def mk(name, pnl, start, end):
        return PeriodStats(period=name, pnl=pnl, start_value=start,
                           end_value=end, vlm=0,
                           roi_pct=(pnl / start * 100) if start > 0 else 0)
    perf = PerformanceSnapshot(
        address="combined",
        day=mk("day", 0, 1900, 2000),
        week=mk("week", 0, 1900, 2000),
        month=mk("month", -200, 0.0, 2000),  # start=0 → fallback
        all_time=mk("allTime", 0, 0, 2000),
        current_account_value=2000,
    )
    msgs = render_daily_report([], [], {}, None, 2000, NOW, performance=perf)
    text = "\n".join(msgs)
    # Implied start = 2200, ROI = -200/2200 = -9.09% → "-9" in text
    assert "-9" in text


def test_monthly_roi_uses_hl_value_when_start_valid():
    """Normal case: start_value > 0, use HL's value directly for month."""
    def mk(name, pnl, start, end):
        return PeriodStats(period=name, pnl=pnl, start_value=start,
                           end_value=end, vlm=0,
                           roi_pct=(pnl / start * 100) if start > 0 else 0)
    perf = PerformanceSnapshot(
        address="combined",
        day=mk("day", 0, 1500, 1500),
        week=mk("week", 0, 1500, 1500),
        month=mk("month", 500, 1000, 1500),  # +50%
        all_time=mk("allTime", 0, 0, 1500),
        current_account_value=1500,
    )
    msgs = render_daily_report([], [], {}, None, 1500, NOW, performance=perf)
    text = "\n".join(msgs)
    assert "+50" in text


def test_alltime_roi_no_fallback_when_implied_start_is_zero():
    """Defensive: if pnl == current_value (implied start = 0), don't divide."""
    def mk(name, pnl, start):
        return PeriodStats(period=name, pnl=pnl, start_value=start,
                           end_value=start + pnl, vlm=0,
                           roi_pct=0)
    perf = PerformanceSnapshot(
        address="combined",
        day=mk("day", 100, 1000), week=mk("week", 0, 0), month=mk("month", 0, 0),
        all_time=mk("allTime", 1000, 0),  # pnl=current → implied start = 0
        current_account_value=1000,
    )
    # should not crash
    msgs = render_daily_report([], [], {}, None, 1000, NOW, performance=perf)
    assert "ETH" not in msgs[0]  # no positions, just sanity


# ---------- Fix 3: SL risk display (UI round 3 simplification) ----------

def test_orphan_shows_max_loss_in_row():
    """Round 3: row shows 'SL: -$X' (max loss in USD), SL price not in row."""
    eth = _pos("ETH", 0.7238, 2173.0, liq_dist=0.0)
    sls = [_sl("ETH", 2122.0)]
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2181.0}, None, 2336, NOW,
        sl_orders=sls,
    )
    text = "\n".join(msgs)
    # Max loss is shown (a few dollars off due to rounding)
    assert "SL: -$42" in text or "SL: -$43" in text
    # SL price itself NOT in the row (simplified)
    assert "$2 122" not in text and "2122" not in text


def test_orphan_no_sl_gets_red_marker():
    """Round 3: 🔴 prefix instead of '⚠️ нет SL'."""
    eth = _pos("ETH", 0.7238, 2173.0, liq_dist=89.4)
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2181.0}, None, 2336, NOW,
        sl_orders=[],
    )
    text = "\n".join(msgs)
    # Red marker on the row
    eth_lines = [l for l in text.split("\n") if "<code>ETH</code>" in l and "LONG" in l]
    assert len(eth_lines) == 1
    assert "🔴" in eth_lines[0]
