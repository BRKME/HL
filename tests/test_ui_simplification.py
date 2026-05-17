"""Tests for UI simplification (round 3, 17 May UX feedback):

1. Header: single line, no 'N кошельков', no Exposure badge, no Top
2. New 'Веса' block: portfolio weights normalised to 100% (not leverage %)
3. Day moves from header into Доходность block
4. Section renamed: 'Ручные / orphan позиции' → 'Ручные позиции'
5. Position row: one line, 'COIN LONG • $X • [24h ±Y%] • SL: -$Z'
   No entry→mark, no current PnL%, no concentration %, no ATR units,
   no liq buffer, no SL price. Just the minimum to know what+24h+max loss.
6. Position without SL: prefixed with 🔴 red marker
7. Alerts simplified:
   - SL_APPROACH / ORPHAN_SL_APPROACH fires ONLY when SL distance < 0.5× ATR
     ('likely intraday'). Other distances are not actionable since SL is set.
   - NO_SL_ORDER stays — promoted to short form '🔴 COIN: нет SL на бирже'
   - Tight-SL alert in short form: '🔴 COIN: SL вышибет внутри дня (X× ATR)'
"""
from datetime import datetime, timezone

import pytest

from src.daily_report import render_daily_report
from src.matcher import MatchResult
from src.monitor_rules import (
    Alert, evaluate_all, RuleConfig, SEV_WARN, SEV_CRITICAL,
    rule_orphan_sl_approach,
)
from src.portfolio import AggregatedPerpPosition
from src.portfolio_performance import PerformanceSnapshot, PeriodStats
from src.sl_visibility import SLOrder


NOW = datetime(2026, 5, 17, 13, 46, tzinfo=timezone.utc)


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


def _perf(day_pnl=60, week_pnl=3, month_pnl=2, current=2347):
    def mk(n, p, s, e):
        return PeriodStats(period=n, pnl=p, start_value=s, end_value=e, vlm=0,
                           roi_pct=(p / s * 100) if s > 0 else 0)
    return PerformanceSnapshot(
        address="combined",
        day=mk("day", day_pnl, current - day_pnl, current),
        week=mk("week", week_pnl, current - week_pnl, current),
        month=mk("month", month_pnl, current - month_pnl, current),
        all_time=mk("allTime", 0, 0, current),
        current_account_value=current,
    )


# ---------- 1. Header: single line, simplified ----------

def test_header_no_wallets_count():
    msgs = render_daily_report([], [], {}, None, 2347, NOW, wallet_count=3,
                                performance=_perf())
    text = msgs[0]
    assert "3 кошелька" not in text
    assert "кошельк" not in text  # no плюрализованная форма


def test_header_no_exposure_badge():
    eth = _pos("ETH", 0.7238, 2173.0)
    tao = _pos("TAO", 3.79, 271.61)
    msgs = render_daily_report(
        [_orphan(eth), _orphan(tao)], [], {"ETH": 2192.0, "TAO": 272.0},
        None, 2347, NOW, performance=_perf(),
    )
    text = msgs[0]
    # Was: '⚠️ Exposure 2.0×' in header
    assert "Exposure" not in text


def test_header_no_top_concentration_in_one_line():
    """Top: ETH 68% removed from header (replaced by separate Веса block)."""
    eth = _pos("ETH", 0.7238, 2173.0)
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2192.0}, None, 2347, NOW,
        performance=_perf(),
    )
    text = msgs[0]
    # First line should not say 'Top:'
    first_line = text.split("\n")[0]
    assert "Top:" not in first_line


def test_header_no_day_pnl_in_first_line():
    """Day moves into Доходность block, not in header."""
    msgs = render_daily_report([], [], {}, None, 2347, NOW, performance=_perf())
    text = msgs[0]
    first_line = text.split("\n")[0]
    assert "Day" not in first_line
    assert "+$60" not in first_line


def test_header_contains_date_and_total():
    msgs = render_daily_report([], [], {}, None, 2347, NOW, performance=_perf())
    text = msgs[0]
    # Date and total still in header
    assert "17 мая 2026" in text
    assert "$2 347" in text


# ---------- 2. Веса block: normalised to 100% ----------

def test_veca_block_present_when_positions():
    """New block: 'Веса: ETH 34% • TAO 22% • ...'."""
    eth = _pos("ETH", 0.7238, 2173.0)
    tao = _pos("TAO", 3.79, 271.61)
    msgs = render_daily_report(
        [_orphan(eth), _orphan(tao)], [], {"ETH": 2192.0, "TAO": 272.0},
        None, 2347, NOW, performance=_perf(),
    )
    text = "\n".join(msgs)
    assert "Веса" in text


def test_veca_normalised_to_100_pct_not_leverage():
    """If exposure = 200% of account ($1587 ETH + $1032 TAO = $2619, account $1300),
    weights should sum to 100% (each weight = pos / total_exposure),
    NOT to 200% (pos / account)."""
    eth = _pos("ETH", 1.0, 1587.0)   # $1587 notional
    tao = _pos("TAO", 1.0, 1032.0)   # $1032 notional
    # account = $1300, total exposure = $2619, leverage = 2.0×
    msgs = render_daily_report(
        [_orphan(eth), _orphan(tao)], [], {"ETH": 1587.0, "TAO": 1032.0},
        None, 1300, NOW, performance=_perf(current=1300),
    )
    text = "\n".join(msgs)
    # Веса line found
    veca_line = next(l for l in text.split("\n") if "Веса" in l)
    # ETH share = 1587/2619 = 60.6%, TAO = 1032/2619 = 39.4%
    # Sum = 100%
    assert "61%" in veca_line or "60%" in veca_line   # ETH
    assert "40%" in veca_line or "39%" in veca_line   # TAO
    # Must NOT show leverage % (would be 122% ETH, 79% TAO of $1300)
    assert "122%" not in veca_line
    assert "79%" not in veca_line


def test_veca_sorted_largest_first():
    """Weight list sorted by size descending."""
    small = _pos("SOPH", 17179.0, 0.008)  # $142
    big = _pos("ETH", 1.0, 1587.0)         # $1587
    mid = _pos("BTC", 0.01, 78000.0)       # $780
    msgs = render_daily_report(
        [_orphan(small), _orphan(big), _orphan(mid)], [],
        {"SOPH": 0.008, "ETH": 1587.0, "BTC": 78000.0},
        None, 2500, NOW, performance=_perf(current=2500),
    )
    text = "\n".join(msgs)
    veca_line = next(l for l in text.split("\n") if "Веса" in l)
    # ETH biggest -> first; SOPH smallest -> last
    eth_pos = veca_line.find("ETH")
    btc_pos = veca_line.find("BTC")
    soph_pos = veca_line.find("SOPH")
    assert eth_pos < btc_pos < soph_pos


def test_veca_block_omitted_when_no_positions():
    msgs = render_daily_report([], [], {}, None, 2347, NOW, performance=_perf())
    text = "\n".join(msgs)
    assert "Веса" not in text


# ---------- 3. Day in Доходность block ----------

def test_dohodnost_contains_day_row():
    msgs = render_daily_report([], [], {}, None, 2347, NOW, performance=_perf())
    text = "\n".join(msgs)
    # Find perf block
    perf_idx = text.find("📈 Доходность")
    assert perf_idx != -1
    perf_section = text[perf_idx:]
    assert "Day" in perf_section or "Сегодня" in perf_section
    assert "+$60" in perf_section


# ---------- 4. Section rename ----------

def test_section_renamed_to_ruchnye_pozicii():
    eth = _pos("ETH", 0.7238, 2173.0)
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2192.0}, None, 2347, NOW,
    )
    text = "\n".join(msgs)
    assert "Ручные позиции" in text
    # Old name gone
    assert "orphan" not in text


# ---------- 5. Position row: minimal one-line format ----------

def test_position_row_is_single_line():
    """Each position renders on ONE line, no second risk line."""
    eth = _pos("ETH", 0.7238, 2173.0)
    sls = [_sl("ETH", 2122.0)]
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2192.0}, None, 2347, NOW, sl_orders=sls,
    )
    text = "\n".join(msgs)
    # Find lines mentioning ETH (excluding header/weights)
    eth_lines = [
        l for l in text.split("\n")
        if "ETH" in l and "Веса" not in l and "HL Portfolio" not in l
    ]
    # Only ONE line for the position
    assert len(eth_lines) == 1


def test_position_row_minimal_content():
    """Format: COIN LONG • $value • [pnl USD] • SL: -$Z
    No entry, no mark, no current PnL%, no concentration %, no ATR, no liq.
    24h ±% replaced with unrealized PnL in USD (round 3 follow-up)."""
    eth = _pos("ETH", 0.7238, 2173.0)
    sls = [_sl("ETH", 2122.0)]  # max loss = $51
    # Give position a non-zero unrealized PnL to verify it shows
    eth = AggregatedPerpPosition(
        coin="ETH", net_size=0.7238, weighted_entry=2173.0, total_pnl=13.75,
        contributors=[("main", 0.7238)], avg_leverage=5,
        max_liquidation_distance_pct=0.0,
    )
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2192.0}, None, 2347, NOW,
        sl_orders=sls,
        prev_day_marks={"ETH": 2177.0},
    )
    text = "\n".join(msgs)
    eth_line = next(l for l in text.split("\n") if "ETH" in l and "LONG" in l)

    # Has the core 4 pieces
    assert "LONG" in eth_line
    assert "$1" in eth_line  # USD value (1587 or similar)
    # PnL USD bracket (with sign) instead of 24h %
    assert "+$13" in eth_line or "+$14" in eth_line
    assert "SL" in eth_line  # max-loss bit

    # Does NOT have removed elements
    assert "@" not in eth_line  # entry @ price gone
    assert "→" not in eth_line  # mark arrow gone
    assert "ATR" not in eth_line  # ATR units gone
    assert "liq buffer" not in eth_line  # liq removed from row
    assert "if hit" not in eth_line  # phrase removed (just '-$Z')
    assert "24h" not in eth_line  # 24h % gone (replaced by PnL USD)


def test_position_max_loss_negative_format():
    """SL: -$7 (negative number — what you lose)."""
    btc = _pos("BTC", 0.00779, 78101.0)
    sls = [_sl("BTC", 77319.0)]
    # max loss = (78222 - 77319) * 0.00779 ≈ $7
    msgs = render_daily_report(
        [_orphan(btc)], [], {"BTC": 78222.0}, None, 2347, NOW, sl_orders=sls,
    )
    text = "\n".join(msgs)
    btc_line = next(l for l in text.split("\n") if "BTC" in l and "LONG" in l)
    # The max-loss number with minus sign (i.e. money lost)
    assert "-$7" in btc_line or "-$6" in btc_line or "-$8" in btc_line


# ---------- 6. Red marker for no-SL position ----------

def test_position_without_sl_has_red_marker():
    eth = _pos("ETH", 0.7238, 2173.0)
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2192.0}, None, 2347, NOW, sl_orders=[],
    )
    text = "\n".join(msgs)
    eth_line = next(l for l in text.split("\n") if "ETH" in l and "LONG" in l)
    assert "🔴" in eth_line


def test_position_with_sl_no_red_marker():
    eth = _pos("ETH", 0.7238, 2173.0)
    sls = [_sl("ETH", 2122.0)]
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2192.0}, None, 2347, NOW, sl_orders=sls,
    )
    text = "\n".join(msgs)
    eth_line = next(l for l in text.split("\n") if "ETH" in l and "LONG" in l)
    assert "🔴" not in eth_line


# ---------- 7. Alert simplification ----------

class _StubSL:
    def __init__(self, trigger, side="long"):
        self.trigger_px = trigger
        self.protects_side = side


def test_orphan_sl_approach_silent_when_atr_distance_above_half():
    """SL at 1.0× ATR distance: SL is doing its job, no need to alert.
    Previously fired at 3% percent threshold; now uses ATR threshold."""
    class _Pos:
        coin = "ETH"; net_size = 0.7238; weighted_entry = 2173.0; side = "long"
    class _M:
        status = "orphan"; position = _Pos()
    sl = _StubSL(2122.0)
    # ATR=$60 → distance=$51 → 0.85× ATR — not tight enough
    alerts = rule_orphan_sl_approach(
        _M(), current_mark=2173.0, sl_order=sl, coin_atr=60.0,
    )
    assert alerts == []


def test_orphan_sl_approach_fires_when_atr_distance_below_half():
    """SL at 0.4× ATR distance: 'likely intraday' — actionable."""
    class _Pos:
        coin = "BTC"; net_size = 0.00779; weighted_entry = 78101.0; side = "long"
    class _M:
        status = "orphan"; position = _Pos()
    sl = _StubSL(77319.0)
    # ATR=$2200 → distance=$903 → 0.41× ATR — tight
    alerts = rule_orphan_sl_approach(
        _M(), current_mark=78222.0, sl_order=sl, coin_atr=2200.0,
    )
    assert len(alerts) == 1
    msg = alerts[0].message
    # Short, descriptive
    assert "BTC" in msg
    assert "ATR" in msg  # mentions volatility units
    # Old verbose 'mark $X within Y% of SL $Z' format gone
    assert "within" not in msg
    assert "of SL" not in msg


def test_orphan_sl_approach_fires_critical_when_past_sl():
    """Past SL — still critical, regardless of ATR."""
    class _Pos:
        coin = "ETH"; net_size = 0.7238; weighted_entry = 2173.0; side = "long"
    class _M:
        status = "orphan"; position = _Pos()
    sl = _StubSL(2200.0)  # SL above current
    alerts = rule_orphan_sl_approach(
        _M(), current_mark=2180.0, sl_order=sl, coin_atr=50.0,
    )
    assert len(alerts) == 1
    assert alerts[0].severity == SEV_CRITICAL


def test_orphan_sl_approach_without_atr_falls_back_to_percent():
    """If ATR unavailable, still fire when very tight (≤1.5% percent fallback)."""
    class _Pos:
        coin = "BTC"; net_size = 0.00779; weighted_entry = 78101.0; side = "long"
    class _M:
        status = "orphan"; position = _Pos()
    sl = _StubSL(77319.0)
    # 1.2% percent (78222 → 77319), no ATR
    alerts = rule_orphan_sl_approach(
        _M(), current_mark=78222.0, sl_order=sl, coin_atr=None,
    )
    # Fires at <=1.5% percent fallback
    assert len(alerts) == 1


def test_orphan_sl_approach_silent_when_no_atr_and_above_percent_fallback():
    """3% from SL, no ATR → silent (SL is fine doing its job)."""
    class _Pos:
        coin = "ETH"; net_size = 0.7238; weighted_entry = 2173.0; side = "long"
    class _M:
        status = "orphan"; position = _Pos()
    sl = _StubSL(2122.0)  # 2.7% below 2181
    alerts = rule_orphan_sl_approach(
        _M(), current_mark=2181.0, sl_order=sl, coin_atr=None,
    )
    assert alerts == []  # 2.7% percent, no ATR → above 1.5% fallback


def test_no_sl_order_alert_text_short_form():
    """'🔴 BTC: нет SL на бирже' rather than verbose form."""
    from src.monitor_rules import rule_no_sl_order
    class _Pos:
        coin = "BTC"; net_size = 0.00779; weighted_entry = 78101.0; side = "long"
    class _M:
        status = "orphan"; position = _Pos()
    alerts = rule_no_sl_order(_M(), sl_order=None)
    assert len(alerts) == 1
    msg = alerts[0].message
    assert "BTC" in msg
    assert "нет SL" in msg


def test_evaluate_all_accepts_coin_atrs():
    """The pipeline lets coin_atrs reach the SL rule."""
    pos = _pos("BTC", 0.00779, 78101.0)
    sl = _sl("BTC", 77319.0)
    alerts = evaluate_all(
        matches=[_orphan(pos)],
        marks={"BTC": 78222.0},
        current_snapshot=None, yesterday_snapshot=None,
        sl_orders=[sl],
        coin_atrs={"BTC": 2200.0},  # tight
    )
    # ORPHAN_SL_APPROACH fires because <0.5× ATR
    rules = {a.rule for a in alerts}
    assert "ORPHAN_SL_APPROACH" in rules


def test_evaluate_all_silent_when_atr_distance_safe():
    pos = _pos("ETH", 0.7238, 2173.0)
    sl = _sl("ETH", 2122.0)
    alerts = evaluate_all(
        matches=[_orphan(pos)],
        marks={"ETH": 2192.0},
        current_snapshot=None, yesterday_snapshot=None,
        sl_orders=[sl],
        coin_atrs={"ETH": 60.0},  # 70/60 = 1.17× — safe
    )
    rules = {a.rule for a in alerts}
    assert "ORPHAN_SL_APPROACH" not in rules


# ---------- 8. Alphabetical sort + PnL USD (round 3 follow-up) ----------

def test_positions_sorted_alphabetically_by_coin():
    """Round 3 follow-up: sort by coin name ascending (not SL distance)."""
    btc  = AggregatedPerpPosition("BTC",  0.01, 78000.0, 0,
        contributors=[("main", 0.01)], avg_leverage=5, max_liquidation_distance_pct=0)
    eth  = AggregatedPerpPosition("ETH",  1.0, 2000.0, 0,
        contributors=[("main", 1.0)], avg_leverage=5, max_liquidation_distance_pct=0)
    zec  = AggregatedPerpPosition("ZEC",  1.0, 500.0, 0,
        contributors=[("main", 1.0)], avg_leverage=5, max_liquidation_distance_pct=0)
    aaa  = AggregatedPerpPosition("AAA",  1.0, 10.0, 0,
        contributors=[("main", 1.0)], avg_leverage=5, max_liquidation_distance_pct=0)
    # Pass in non-alphabetical order
    msgs = render_daily_report(
        [_orphan(zec), _orphan(eth), _orphan(btc), _orphan(aaa)], [],
        {"BTC": 78000, "ETH": 2000, "ZEC": 500, "AAA": 10},
        None, 100000, NOW,
    )
    text = "\n".join(msgs)
    # Order in output: AAA, BTC, ETH, ZEC (alphabetical)
    aaa_pos = text.find("<code>AAA</code>")
    btc_pos = text.find("<code>BTC</code>")
    eth_pos = text.find("<code>ETH</code>")
    zec_pos = text.find("<code>ZEC</code>")
    assert aaa_pos < btc_pos < eth_pos < zec_pos


def test_position_shows_unrealized_pnl_usd_with_sign():
    """[+$14] for profit, [-$10] for loss — total_pnl from HL."""
    profit = AggregatedPerpPosition(
        coin="ETH", net_size=1.0, weighted_entry=2000.0, total_pnl=14.50,
        contributors=[("main", 1.0)], avg_leverage=5,
        max_liquidation_distance_pct=0,
    )
    loss = AggregatedPerpPosition(
        coin="BTC", net_size=0.01, weighted_entry=78000.0, total_pnl=-9.75,
        contributors=[("main", 0.01)], avg_leverage=5,
        max_liquidation_distance_pct=0,
    )
    msgs = render_daily_report(
        [_orphan(profit), _orphan(loss)], [],
        {"ETH": 2014.5, "BTC": 77000.0}, None, 10000, NOW,
    )
    text = "\n".join(msgs)
    eth_line = next(l for l in text.split("\n") if "ETH" in l and "LONG" in l)
    btc_line = next(l for l in text.split("\n") if "BTC" in l and "LONG" in l)
    # Profit shows with +
    assert "[+$14]" in eth_line or "[+$15]" in eth_line
    # Loss shows with -
    assert "[-$10]" in btc_line or "[-$9]" in btc_line


def test_position_zero_pnl_still_shown():
    """[+$0] when position is flat — consistent format."""
    flat = AggregatedPerpPosition(
        coin="ETH", net_size=1.0, weighted_entry=2000.0, total_pnl=0.0,
        contributors=[("main", 1.0)], avg_leverage=5,
        max_liquidation_distance_pct=0,
    )
    msgs = render_daily_report(
        [_orphan(flat)], [], {"ETH": 2000.0}, None, 10000, NOW,
    )
    text = "\n".join(msgs)
    eth_line = next(l for l in text.split("\n") if "ETH" in l and "LONG" in l)
    # Brackets present, value is $0 with some sign
    assert "[$0]" in eth_line or "[+$0]" in eth_line or "[-$0]" in eth_line


def test_24h_pct_no_longer_in_row():
    """24h ±% completely gone — replaced by total_pnl USD."""
    eth = AggregatedPerpPosition(
        coin="ETH", net_size=1.0, weighted_entry=2000.0, total_pnl=14.0,
        contributors=[("main", 1.0)], avg_leverage=5,
        max_liquidation_distance_pct=0,
    )
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2014.0}, None, 10000, NOW,
        prev_day_marks={"ETH": 2000.0},  # 24h+0.7% — should NOT appear
    )
    text = "\n".join(msgs)
    eth_line = next(l for l in text.split("\n") if "ETH" in l and "LONG" in l)
    assert "24h" not in eth_line
    assert "+0.7%" not in eth_line
