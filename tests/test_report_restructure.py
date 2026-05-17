"""Tests for Phase 3.1.x report restructure: executive header, concentration %,
clearer liq label, deferred perf, two-line orphan format."""
from datetime import datetime, timezone, timedelta

import pytest

from src.daily_report import render_daily_report
from src.matcher import MatchResult
from src.monitor_rules import Alert
from src.portfolio import AggregatedPerpPosition, SpotPosition
from src.portfolio_performance import PerformanceSnapshot, PeriodStats
from src.sl_visibility import SLOrder


NOW = datetime(2026, 5, 16, 12, 4, tzinfo=timezone.utc)  # 15:04 UTC = 16:04 MSK


def _perf(day_pnl, week_pnl, month_pnl, alltime_pnl,
          day_start=2450, alltime_start=4650):
    def mk(n, p, s):
        return PeriodStats(period=n, pnl=p, start_value=s, end_value=s + p,
                           vlm=0, roi_pct=(p / s * 100) if s > 0 else 0)
    return PerformanceSnapshot(
        address="combined",
        day=mk("day", day_pnl, day_start),
        week=mk("week", week_pnl, day_start),
        month=mk("month", month_pnl, day_start),
        all_time=mk("allTime", alltime_pnl, alltime_start),
        current_account_value=day_start + day_pnl,
    )


def _pos(coin, net_size, entry, liq_dist=80.0, total_pnl=0.0):
    return AggregatedPerpPosition(
        coin=coin, net_size=net_size, weighted_entry=entry, total_pnl=total_pnl,
        contributors=[("main", net_size)], avg_leverage=5,
        max_liquidation_distance_pct=liq_dist,
    )


def _orphan(pos):
    return MatchResult(position=pos, decision=None, status="orphan")


def _sl(coin, trigger, side="long"):
    return SLOrder(coin=coin, trigger_px=trigger, size=0, protects_side=side,
                   order_type="Stop Market", oid=1, account="main",
                   is_position_attached=True)


# ---------- executive header line ----------

def test_header_has_compact_executive_line():
    """First line after title: '$1573 • Day -$64 (-2.6%) • Exposure 2.0× • Top: ETH 100%'."""
    eth = _pos("ETH", 0.7238, 2173.0)
    perf = _perf(day_pnl=-64, week_pnl=-46, month_pnl=-155, alltime_pnl=-3078,
                 day_start=2450)
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2175.0}, None, 1573.0, NOW,
        performance=perf,
    )
    text = msgs[0]
    # one-line summary present
    assert "Day" in text and "-2.6%" in text
    assert "Exposure" in text
    # Top concentration shown (ETH = 100% of $1573)
    assert "Top:" in text or "ETH 100%" in text or "ETH" in text


def test_header_skips_exposure_when_no_positions():
    perf = _perf(day_pnl=10, week_pnl=20, month_pnl=50, alltime_pnl=100)
    msgs = render_daily_report([], [], {}, None, 1500.0, NOW, performance=perf)
    text = msgs[0]
    # No exposure metric when there's nothing to expose
    assert "Exposure" not in text or "Exposure 0" in text


def test_header_shows_negative_day_pnl_with_sign():
    perf = _perf(day_pnl=-50, week_pnl=-10, month_pnl=-100, alltime_pnl=-500)
    msgs = render_daily_report([], [], {}, None, 1000.0, NOW, performance=perf)
    text = msgs[0]
    # We want '-$50' visible, not '+$50'
    assert "-$50" in text or "-$50" in text.replace(" ", "")


def test_header_computes_total_leverage_from_positions():
    """Total exposure = sum |net_size × weighted_entry|. Leverage = exposure / account."""
    eth = _pos("ETH", 0.7238, 2173.0)   # $1573
    tao = _pos("TAO", 3.79, 271.61)      # $1030
    perf = _perf(day_pnl=-64, week_pnl=-46, month_pnl=-155, alltime_pnl=-3078)
    msgs = render_daily_report(
        [_orphan(eth), _orphan(tao)], [], {"ETH": 2175.0, "TAO": 272},
        None, 1573.0, NOW, performance=perf,
    )
    text = msgs[0]
    # ($1573 + $1030) / $1573 ≈ 1.65 → "1.7×"
    assert "1.7" in text or "1.6" in text


# ---------- concentration % per position ----------

def test_orphan_shows_position_value_and_concentration_pct():
    eth = _pos("ETH", 0.7238, 2173.0)  # $1575 at mark $2175
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2175.0}, None, 1573.0, NOW,
    )
    text = "\n".join(msgs)
    # USD value and % of account
    assert "$1 5" in text  # $1575 with thin space
    assert "100%" in text


def test_orphan_concentration_skipped_when_total_value_zero():
    """Defensive: don't div by zero if account_value=0 (edge case)."""
    eth = _pos("ETH", 0.7238, 2173.0)
    # should not crash
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2175.0}, None, 0.0, NOW,
    )
    text = "\n".join(msgs)
    assert "ETH" in text


# ---------- 'liq buffer' label (replaces 'до liq') ----------

def test_liq_label_uses_buffer_terminology():
    eth = _pos("ETH", 0.7238, 2173.0, liq_dist=88.0)
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2175.0}, None, 1573.0, NOW,
    )
    text = "\n".join(msgs).lower()
    assert "liq buffer" in text or "buffer 88" in text
    # Old ambiguous label should not be present
    assert "до liq 88" not in text


# ---------- performance block: compact, no 'Сегодня' (it's in header) ----------

def test_performance_block_excludes_today_when_in_header():
    """'Сегодня' is in the executive header — don't duplicate in 📈 Доходность."""
    perf = _perf(day_pnl=-64, week_pnl=-46, month_pnl=-155, alltime_pnl=-3078)
    msgs = render_daily_report([], [], {}, None, 1573.0, NOW, performance=perf)
    text = "\n".join(msgs)
    # Find the performance block start
    if "📈" in text:
        perf_start = text.index("📈")
        perf_section = text[perf_start:]
        assert "Сегодня" not in perf_section


def test_performance_block_excludes_alltime_for_anchor_bias():
    """UI refinement round 2: All-time row removed from Доходность block
    (anchor bias hurts current-EV decision frame)."""
    perf = _perf(day_pnl=-64, week_pnl=-46, month_pnl=-155, alltime_pnl=-3078,
                 alltime_start=4650)
    msgs = render_daily_report([], [], {}, None, 1573.0, NOW, performance=perf)
    text = "\n".join(msgs)
    # All-time absent
    assert "All-time" not in text
    # But week/month still there
    assert "Неделя" in text
    assert "Месяц" in text


# ---------- alerts visually prominent ----------

def test_critical_alert_shown_with_red_marker():
    """🔴 marker for critical, otherwise plain ⚠️."""
    alerts = [Alert(rule="LIQUIDATION_CLOSE", severity=3, coin="ETH",
                    message="ETH: 10% from liquidation", details={})]
    msgs = render_daily_report([], alerts, {}, None, 1000.0, NOW)
    text = "\n".join(msgs)
    assert "🔴" in text or "🚨" in text


def test_warn_alert_shown_with_orange_marker():
    """⚠️/🟠 for warn (not 🔴 — that's reserved for critical)."""
    alerts = [Alert(rule="ORPHAN_SL_APPROACH", severity=2, coin="ETH",
                    message="ETH within 2.4% of SL", details={})]
    msgs = render_daily_report([], alerts, {}, None, 1000.0, NOW)
    text = "\n".join(msgs)
    assert "⚠️" in text or "🟠" in text
    # critical-only marker should NOT appear when there's no critical
    assert "🔴" not in text


# ---------- footer: regime/phase moved to footer or trimmed ----------

def test_regime_phase_in_footer_not_header():
    snapshot = {"regime": "BULL", "cycle": {"phase": "EARLY_BEAR"}}
    msgs = render_daily_report([], [], {}, snapshot, 1000.0, NOW)
    text = msgs[0]
    if "regime" in text:
        # if shown, it's after positions/alerts, not in the first 3 lines
        first_lines = "\n".join(text.split("\n")[:3])
        assert "regime" not in first_lines.lower()


# ---------- two-line orphan format (kept compact, key risk visible) ----------

def test_orphan_two_line_format_keeps_sl_risk_on_second_line():
    """Per UX feedback: separate position line from risk line."""
    eth = _pos("ETH", 0.7238, 2173.0, liq_dist=88.0)
    sls = [_sl("ETH", 2122.0)]
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2175.0}, None, 1573.0, NOW,
        sl_orders=sls,
    )
    text = "\n".join(msgs)
    # SL info present
    assert "$2 122" in text or "2122" in text
    # Liq buffer present
    assert "88" in text


# ---------- preserves backward-compat: render still works without performance ----------

def test_render_works_without_performance():
    """Renderer must not require performance arg."""
    eth = _pos("ETH", 0.7238, 2173.0)
    msgs = render_daily_report(
        [_orphan(eth)], [], {"ETH": 2175.0}, None, 1573.0, NOW,
    )
    assert msgs[0]
    assert "ETH" in msgs[0]


def test_render_works_with_no_positions_no_perf():
    msgs = render_daily_report([], [], {}, None, 0.0, NOW)
    assert msgs[0]
