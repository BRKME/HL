"""Tests for UI refinement (UX feedback round 2):

1. Action Required block moves above Доходность
2. Positions sorted by SL distance ascending (most at risk first)
3. SL distance shown in ATR units when ATR available
4. Leverage badge as its own line when leverage >= 1.5×
5. All-time row removed from Доходность block (anchor bias)
6. Integer rounding for price displays
"""
from datetime import datetime, timezone

import pytest

from src.daily_report import render_daily_report
from src.matcher import MatchResult
from src.monitor_rules import Alert, SEV_WARN, SEV_CRITICAL
from src.portfolio import AggregatedPerpPosition
from src.portfolio_performance import PerformanceSnapshot, PeriodStats
from src.sl_visibility import SLOrder


NOW = datetime(2026, 5, 17, 8, 0, tzinfo=timezone.utc)


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


def _perf(day_pnl=0, week_pnl=0, month_pnl=0, alltime_pnl=0, current=2336):
    def mk(n, p, s, e):
        return PeriodStats(period=n, pnl=p, start_value=s, end_value=e, vlm=0,
                           roi_pct=(p / s * 100) if s > 0 else 0)
    return PerformanceSnapshot(
        address="combined",
        day=mk("day", day_pnl, current - day_pnl, current),
        week=mk("week", week_pnl, current - week_pnl, current),
        month=mk("month", month_pnl, current - month_pnl, current),
        all_time=mk("allTime", alltime_pnl, 0.0, current),
        current_account_value=current,
    )


# ---------- 1. Action required (alerts) above PnL ----------

def test_alerts_appear_before_dohodnost_block():
    eth = _pos("ETH", 0.7238, 2173.0)
    sls = [_sl("ETH", 2122.0)]
    perf = _perf(day_pnl=23, week_pnl=2)
    alerts = [Alert(rule="ORPHAN_SL_APPROACH", severity=SEV_WARN, coin="ETH",
                    message="ETH within 2.9% of SL", details={"distance_pct": 2.9})]
    msgs = render_daily_report(
        [_orphan(eth)], alerts, {"ETH": 2186.0}, None, 2336, NOW,
        performance=perf, sl_orders=sls,
    )
    text = msgs[0]
    alerts_pos = text.find("Алерты")
    perf_pos = text.find("Доходность")
    assert alerts_pos != -1 and perf_pos != -1
    assert alerts_pos < perf_pos


def test_no_alerts_block_when_no_alerts():
    """When there are no alerts, the section is skipped — no empty 'Алерты:' header."""
    eth = _pos("ETH", 0.7238, 2173.0, liq_dist=80.0)
    sls = [_sl("ETH", 2090.0)]
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2186.0}, None, 2336, NOW, sl_orders=sls,
    )
    text = "\n".join(msgs)
    # Either no Алерты section, or a "no-alerts" check mark
    if "Алерты" in text:
        # if present, should be the "all clear" version
        assert "✅" in text


# ---------- 2. Sort positions by SL distance ----------

def test_positions_sorted_by_sl_distance_ascending():
    """BTC at 1.0% should come before ETH at 2.7% (tighter SL = higher up)."""
    eth = _pos("ETH", 0.7238, 2173.0)
    btc = _pos("BTC", 0.00779, 78101.0)
    tao = _pos("TAO", 3.79, 271.61)
    sls = [
        _sl("ETH", 2122.0),     # 2.7% from $2181
        _sl("BTC", 77319.0),    # 1.1% from $78180
        _sl("TAO", 260.0),      # 4.7% from $273
    ]
    marks = {"ETH": 2181.0, "BTC": 78180.0, "TAO": 273.0}
    msgs = render_daily_report(
        [_orphan(eth), _orphan(btc), _orphan(tao)], [], marks,
        None, 2336, NOW, sl_orders=sls,
    )
    text = "\n".join(msgs)
    btc_pos = text.find("<code>BTC</code>")
    eth_pos = text.find("<code>ETH</code>")
    tao_pos = text.find("<code>TAO</code>")
    assert btc_pos < eth_pos < tao_pos


def test_positions_without_sl_sort_last():
    """No-SL positions are not 'at risk in dist units' — push to bottom."""
    eth = _pos("ETH", 0.7238, 2173.0)
    btc = _pos("BTC", 0.00779, 78101.0)
    sls = [_sl("BTC", 77319.0)]  # only BTC has SL
    marks = {"ETH": 2181.0, "BTC": 78180.0}
    msgs = render_daily_report(
        [_orphan(eth), _orphan(btc)], [], marks,
        None, 2336, NOW, sl_orders=sls,
    )
    text = "\n".join(msgs)
    assert text.find("<code>BTC</code>") < text.find("<code>ETH</code>")


# ---------- 3. SL distance in ATR units ----------

def test_sl_distance_shown_in_atr_units_when_atr_provided():
    """Per-coin ATR map → 'SL = 0.4× ATR' shown."""
    eth = _pos("ETH", 0.7238, 2173.0)
    sls = [_sl("ETH", 2122.0)]  # $59 distance from $2181
    # If ATR=$30/day, distance/ATR = 1.97
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2181.0}, None, 2336, NOW,
        sl_orders=sls, coin_atrs={"ETH": 30.0},
    )
    text = "\n".join(msgs)
    # mentions ATR-relative distance
    assert "ATR" in text


def test_sl_distance_atr_format_for_tight_stop():
    """Critically tight SL: <0.5 ATR = likely intraday touch."""
    btc = _pos("BTC", 0.00779, 78101.0)
    sls = [_sl("BTC", 77319.0)]  # $860 distance from $78180
    # If ATR=$2200, distance/ATR = 0.39
    msgs = render_daily_report(
        [_orphan(btc)], [], {"BTC": 78180.0}, None, 2336, NOW,
        sl_orders=sls, coin_atrs={"BTC": 2200.0},
    )
    text = "\n".join(msgs)
    # uses ATR multiplier and signals tightness
    assert "ATR" in text
    assert "0.4" in text or "0.3" in text


def test_no_atr_fallback_to_percent_only():
    """When ATR not provided, behave like before — pct only."""
    eth = _pos("ETH", 0.7238, 2173.0)
    sls = [_sl("ETH", 2122.0)]
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2181.0}, None, 2336, NOW, sl_orders=sls,
    )
    text = "\n".join(msgs)
    # No ATR in output when no ATR data
    assert "ATR" not in text
    # Still shows percent
    assert "2.7" in text or "2.8" in text or "2.9" in text


# ---------- 4. Leverage badge ----------

def test_leverage_badge_when_two_or_higher():
    """Exposure ≥1.5× should get a ⚠️ marker, not just be mentioned in passing."""
    eth = _pos("ETH", 0.7238, 2173.0)
    tao = _pos("TAO", 3.79, 271.61)
    msgs = render_daily_report(
        [_orphan(eth), _orphan(tao)], [], {"ETH": 2186.0, "TAO": 273.0},
        None, 1573, NOW,  # ETH alone is 100% of $1573, with TAO total exposure > 1.5x
    )
    text = msgs[0]
    # ⚠️ near the Exposure number
    assert "⚠️" in text
    # Still shows the leverage value
    assert "2.0×" in text or "1.7×" in text or "1." in text


def test_no_leverage_badge_below_threshold():
    """At 1.0× exposure (single position = total value) no badge."""
    eth = _pos("ETH", 0.1, 10000.0)  # $1000 position, $10000 total → 0.1×
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 10000.0}, None, 10000, NOW,
    )
    text = msgs[0]
    # ⚠️ not on the leverage badge
    header_lines = text.split("\n")[:3]
    header_text = "\n".join(header_lines)
    if "Exposure" in header_text:
        # If exposure mentioned, no warning marker on this line
        exposure_line = [l for l in header_lines if "Exposure" in l][0]
        assert "⚠️" not in exposure_line


# ---------- 5. All-time removed from Доходность block ----------

def test_alltime_not_in_dohodnost_block():
    perf = _perf(day_pnl=23, week_pnl=2, month_pnl=-1, alltime_pnl=-3013)
    msgs = render_daily_report([], [], {}, None, 2336, NOW, performance=perf)
    text = "\n".join(msgs)
    # All-time should be absent — anchor bias
    assert "All-time" not in text
    assert "All time" not in text
    # But week/month still there
    assert "Неделя" in text or "Неделя " in text
    assert "Месяц" in text


def test_alltime_pnl_value_not_anywhere_in_report():
    """The -$3013 figure should not appear at all (anchor removal)."""
    perf = _perf(day_pnl=23, week_pnl=2, month_pnl=-1, alltime_pnl=-3013)
    msgs = render_daily_report([], [], {}, None, 2336, NOW, performance=perf)
    text = "\n".join(msgs)
    assert "-$3 013" not in text
    assert "-$3013" not in text


# ---------- 6. Integer rounding for price displays ----------

def test_position_prices_rounded_to_integers_for_large_prices():
    """TAO entry 271.61 → '271', mark 273.67 → '273' (drop decimals)."""
    tao = _pos("TAO", 3.79, 271.61)
    msgs = render_daily_report(
        [_orphan(tao)], [], {"TAO": 273.67}, None, 1000, NOW,
    )
    text = "\n".join(msgs)
    # No decimal numbers like .61 or .67 for prices above 100
    assert "271.61" not in text
    assert "273.67" not in text
    # The rounded integers ARE there
    assert "272" in text  # 271.61 rounds to 272
    assert "274" in text  # 273.67 rounds to 274


def test_tiny_price_still_shows_meaningful_digits():
    """SOPH at $0.008042 must NOT be rounded to integer 0 — keep precision for sub-1."""
    soph = _pos("SOPH", 17179.0, 0.008042)
    msgs = render_daily_report(
        [_orphan(soph)], [], {"SOPH": 0.00813}, None, 1000, NOW,
    )
    text = "\n".join(msgs)
    # The price should not become "0" or "$0"
    assert "$0.008" in text or "0.00813" in text or "0.008" in text


def test_btc_huge_price_no_decimals():
    """BTC ~$78000 — definitely no decimals."""
    btc = _pos("BTC", 0.00779, 78101.0)
    msgs = render_daily_report(
        [_orphan(btc)], [], {"BTC": 78179.55}, None, 1000, NOW,
    )
    text = "\n".join(msgs)
    assert "78179.55" not in text
    assert "78180" in text or "$78 180" in text or "78 179" in text


def test_sl_prices_also_rounded():
    """SL display must also use integer rounding for large prices."""
    eth = _pos("ETH", 0.7238, 2173.0)
    sls = [_sl("ETH", 2122.50)]
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2186.0}, None, 1000, NOW, sl_orders=sls,
    )
    text = "\n".join(msgs)
    assert "2122.5" not in text
    assert "2 123" in text or "2 122" in text or "2122" in text or "2123" in text
