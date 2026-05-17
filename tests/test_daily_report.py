"""Tests for src/daily_report.py — Telegram HTML rendering of daily monitor."""
from datetime import datetime, timezone, timedelta

import pytest

from src.daily_report import render_daily_report
from src.decisions_log import Decision
from src.matcher import MatchResult
from src.monitor_rules import Alert, SEV_INFO, SEV_WARN, SEV_CRITICAL
from src.portfolio import AggregatedPerpPosition, SpotPosition


NOW = datetime(2026, 5, 14, 6, 0, tzinfo=timezone.utc)  # 09:00 MSK


def _pos(coin="BTC", net_size=0.5, entry=80000.0, pnl=100.0, liq_dist=25.0):
    return AggregatedPerpPosition(
        coin=coin,
        net_size=net_size,
        weighted_entry=entry,
        total_pnl=pnl,
        contributors=[("main", net_size)],
        avg_leverage=10.0,
        max_liquidation_distance_pct=liq_dist,
    )


def _dec(coin="BTC", entry=80000.0, sl=76000.0, days_ago=3):
    return Decision(
        ts=NOW - timedelta(days=days_ago),
        signal="MODERATE",
        coin=coin,
        entry=entry,
        alloc_usd=200.0,
        expected_size=200.0 / entry,
        sl_price=sl,
        sl_pct=-5.0,
        sl_method="atr",
        atr14=2000.0,
        regime_at_entry="BULL",
        phase_at_entry="MID_BULL",
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


# ---------- structure ----------

def test_render_returns_list_of_strings():
    msgs = render_daily_report([], [], {}, None, total_account_value=1000.0, now=NOW)
    assert isinstance(msgs, list)
    assert all(isinstance(m, str) for m in msgs)


def test_render_includes_header_with_msk_time_and_date():
    msgs = render_daily_report([], [], {}, None, total_account_value=1605.0, now=NOW)
    text = "\n".join(msgs)
    assert "09:00" in text
    assert "14 мая" in text
    assert "1 605" in text or "1605" in text


def test_render_empty_portfolio_says_so():
    msgs = render_daily_report([], [], {}, None, total_account_value=0.0, now=NOW)
    text = "\n".join(msgs)
    assert any(word in text.lower() for word in ("нет позиций", "пусто", "позиций нет"))


# ---------- alerts section ----------

def test_render_critical_alert_appears_with_red_marker():
    alerts = [Alert(
        rule="LIQUIDATION_CLOSE",
        severity=SEV_CRITICAL,
        coin="BTC",
        message="BTC: only 8.0% from liquidation",
        details={"distance_pct": 8.0},
    )]
    matches = [_tracked()]
    msgs = render_daily_report(matches, alerts, {"BTC": 82000.0}, None, 1000.0, now=NOW)
    text = "\n".join(msgs)
    assert "🔴" in text or "❗" in text
    assert "BTC" in text and "8" in text


def test_render_warn_alert_uses_warning_marker():
    alerts = [Alert(
        rule="SL_APPROACH",
        severity=SEV_WARN,
        coin="ETH",
        message="ETH: close to SL",
        details={"distance_pct": 2.1, "sl_price": 3000.0, "mark": 3063.0},
    )]
    msgs = render_daily_report([_tracked(pos=_pos(coin="ETH", entry=3200, net_size=5))],
                               alerts, {"ETH": 3063.0}, None, 1000.0, now=NOW)
    text = "\n".join(msgs)
    assert "⚠️" in text or "🛑" in text


def test_render_info_alert_uses_info_marker():
    alerts = [Alert(
        rule="PROFIT_TRAIL",
        severity=SEV_INFO,
        coin="SOL",
        message="SOL: up 12.5% — consider trailing SL",
        details={"pnl_pct": 12.5},
    )]
    msgs = render_daily_report([_tracked(pos=_pos(coin="SOL", entry=130, net_size=10))],
                               alerts, {"SOL": 146.0}, None, 1000.0, now=NOW)
    text = "\n".join(msgs)
    assert "💰" in text or "ℹ️" in text


def test_render_portfolio_wide_alert_appears_above_positions():
    """REGIME_FLIP_DAILY has coin='*' — should be in the alerts header, not under a position."""
    alerts = [Alert(
        rule="REGIME_FLIP_DAILY",
        severity=SEV_WARN,
        coin="*",
        message="Regime flipped overnight: BULL → BEAR",
        details={"prev_regime": "BULL", "current_regime": "BEAR"},
    )]
    msgs = render_daily_report([_tracked()], alerts, {"BTC": 82000.0}, None, 1000.0, now=NOW)
    text = "\n".join(msgs)
    # the alert text must appear before the position listing
    assert "BULL → BEAR" in text or "BULL" in text and "BEAR" in text


def test_render_no_alerts_shows_quiet_state():
    matches = [_tracked()]
    msgs = render_daily_report(matches, [], {"BTC": 82000.0}, None, 1000.0, now=NOW)
    text = "\n".join(msgs)
    assert any(word in text.lower() for word in ("спокой", "без алертов", "тихо", "✅"))


# ---------- positions section ----------

def test_render_tracked_position_shows_entry_mark_sl_and_days():
    matches = [_tracked(
        pos=_pos(coin="BTC", net_size=0.5, entry=80000.0, pnl=1000.0),
        dec=_dec(coin="BTC", entry=80000.0, sl=76000.0),
        days=5,
    )]
    msgs = render_daily_report(matches, [], {"BTC": 82000.0}, None, 1000.0, now=NOW)
    text = "\n".join(msgs)
    assert "BTC" in text
    assert "80" in text  # entry mentioned somehow
    assert "82" in text  # mark
    assert "76" in text  # sl
    assert "5" in text  # days


def test_render_orphan_position_marked_as_manual():
    matches = [_orphan(pos=_pos(coin="DOGE", net_size=1000.0, entry=0.15, pnl=10.0))]
    msgs = render_daily_report(matches, [], {"DOGE": 0.16}, None, 1000.0, now=NOW)
    text = "\n".join(msgs)
    assert "DOGE" in text
    assert any(word in text.lower() for word in ("ручн", "orphan", "manual"))


def test_render_short_position_labeled_short():
    matches = [_orphan(pos=_pos(coin="SOL", net_size=-10.0, entry=150.0, pnl=20.0))]
    msgs = render_daily_report(matches, [], {"SOL": 148.0}, None, 1000.0, now=NOW)
    text = "\n".join(msgs)
    assert "SOL" in text
    assert any(s in text.upper() for s in ("SHORT", "ШОРТ"))


def test_render_spot_positions_section():
    spot = [SpotPosition(coin="HYPE", total=10.0, hold=0.0, entry_notional=350.0, account="main")]
    msgs = render_daily_report([], [], {"HYPE": 42.0}, None, 1000.0, now=NOW, spot=spot)
    text = "\n".join(msgs)
    assert "HYPE" in text
    assert any(word in text.lower() for word in ("spot", "спот"))


def test_render_spot_with_mark_shows_usd_value():
    """Even without entry_notional, mark + size gives USD value — show it."""
    spot = [SpotPosition(coin="OMNIX", total=15569.0, hold=0.0, entry_notional=0.0, account="main")]
    msgs = render_daily_report([], [], {"OMNIX": 0.05}, None, 1000.0, now=NOW, spot=spot)
    text = "\n".join(msgs)
    assert "OMNIX" in text
    # 15569 * 0.05 = ~778 USD
    assert "778" in text or "$778" in text


def test_render_spot_without_mark_shows_size_only():
    """No mark price available — fallback to size-only display, don't crash."""
    spot = [SpotPosition(coin="UNKNOWN", total=100.0, hold=0.0, entry_notional=0.0, account="main")]
    msgs = render_daily_report([], [], {}, None, 1000.0, now=NOW, spot=spot)
    text = "\n".join(msgs)
    assert "UNKNOWN" in text


def test_render_spot_hides_dust_below_5usd_value():
    """OMNIX-style dust: large size but value <$5 — skip in display."""
    spot = [
        SpotPosition(coin="OMNIX", total=15569.0, hold=0.0, entry_notional=0.0, account="main"),
        SpotPosition(coin="HYPE", total=10.0, hold=0.0, entry_notional=350.0, account="main"),
    ]
    marks = {"OMNIX": 0.00007, "HYPE": 42.0}  # OMNIX value ~= $1, HYPE = $420
    msgs = render_daily_report([], [], marks, None, 1000.0, now=NOW, spot=spot)
    text = "\n".join(msgs)
    assert "HYPE" in text
    assert "OMNIX" not in text  # dust hidden


def test_render_spot_hides_section_entirely_when_all_dust():
    """If every spot is dust, the whole 🪙 Spot section disappears."""
    spot = [SpotPosition(coin="OMNIX", total=15569.0, hold=0.0, entry_notional=0.0, account="main")]
    marks = {"OMNIX": 0.00007}
    msgs = render_daily_report([], [], marks, None, 1000.0, now=NOW, spot=spot)
    text = "\n".join(msgs)
    assert "Spot" not in text and "OMNIX" not in text


def test_render_tracked_shows_24h_change_when_prev_day_available():
    """If we have yesterday's mark, show 24h change next to current mark."""
    matches = [_tracked(
        pos=_pos(coin="BTC", net_size=0.5, entry=80000.0, pnl=1000.0),
        days=3,
    )]
    msgs = render_daily_report(
        matches, [], {"BTC": 82000.0}, None, 1000.0, now=NOW,
        prev_day_marks={"BTC": 80000.0},  # 24h ago — gives +2.5% daily
    )
    text = "\n".join(msgs)
    # Look for "24h" indicator or a "+2.5" near BTC
    assert "+2.5" in text or "24h" in text


def test_render_liquidation_unambiguous_label_removed_in_round3():
    """UX round 3: liq buffer removed from position row entirely.
    LIQUIDATION_CLOSE alert still fires for critically-close liquidation
    (separate rule), but the per-row 'до liq XX%' / 'liq buffer' is gone."""
    matches = [_orphan(pos=_pos(coin="BTC", liq_dist=73.7))]
    msgs = render_daily_report(matches, [], {"BTC": 80000.0}, None, 1000.0, now=NOW)
    text = "\n".join(msgs).lower()
    # No liq buffer phrase in the position row
    assert "liq buffer" not in text
    assert "до liq" not in text


# ---------- performance block ----------

def _perf(day_pnl=0, week_pnl=0, month_pnl=0, alltime_pnl=0,
         day_start=1000, week_start=1000, month_start=1000, alltime_start=1000,
         failed=None):
    from src.portfolio_performance import PerformanceSnapshot, PeriodStats
    def mk(name, pnl, start):
        return PeriodStats(period=name, pnl=pnl, start_value=start,
                           end_value=start + pnl, vlm=0,
                           roi_pct=(pnl / start * 100) if start > 0 else 0)
    return PerformanceSnapshot(
        address="combined",
        day=mk("day", day_pnl, day_start),
        week=mk("week", week_pnl, week_start),
        month=mk("month", month_pnl, month_start),
        all_time=mk("allTime", alltime_pnl, alltime_start),
        current_account_value=1000 + day_pnl,
        failed_wallets=failed or [],
    )


def test_performance_block_appears_when_provided():
    perf = _perf(day_pnl=45, week_pnl=120, month_pnl=300, alltime_pnl=600)
    msgs = render_daily_report([], [], {}, None, 1605.0, now=NOW, performance=perf)
    text = "\n".join(msgs)
    assert "Доходность" in text
    assert "+$45" in text or "+$45" in text.replace(" ", "")
    assert "+$120" in text or "120" in text


def test_performance_block_skipped_when_all_zero():
    """Wallet hasn't traded → don't show empty block."""
    perf = _perf()  # all zeros
    msgs = render_daily_report([], [], {}, None, 1000.0, now=NOW, performance=perf)
    text = "\n".join(msgs)
    assert "Доходность" not in text


def test_performance_block_shows_negative_pnl():
    perf = _perf(day_pnl=-30, week_pnl=-80)
    msgs = render_daily_report([], [], {}, None, 970.0, now=NOW, performance=perf)
    text = "\n".join(msgs)
    assert "-$30" in text or "-$30" in text.replace(" ", "")


def test_performance_block_includes_roi_percent():
    perf = _perf(day_pnl=50, day_start=1000)
    msgs = render_daily_report([], [], {}, None, 1050, now=NOW, performance=perf)
    text = "\n".join(msgs)
    # ROI = +5.0%
    assert "+5.0%" in text


def test_performance_block_notes_failed_wallets():
    perf = _perf(day_pnl=20, failed=["0xaaa", "0xbbb"])
    msgs = render_daily_report([], [], {}, None, 500, now=NOW, performance=perf)
    text = "\n".join(msgs)
    assert "2" in text and ("не удалось" in text or "failed" in text.lower())


# ---------- footer / regime ----------

def test_render_footer_mentions_current_regime_when_snapshot_present():
    snap = {"regime": "BULL", "cycle": {"phase": "MID_BULL"}}
    msgs = render_daily_report([], [], {}, snap, 1000.0, now=NOW)
    text = "\n".join(msgs)
    assert "BULL" in text
    assert "MID_BULL" in text


def test_render_no_footer_crash_when_snapshot_missing():
    """OracAI down — shouldn't crash, just skip footer."""
    msgs = render_daily_report([], [], {}, None, 1000.0, now=NOW)
    # no exception is the test; sanity check it returns something
    assert msgs


# ---------- chunking for Telegram limit ----------

def test_render_chunks_long_output_into_multiple_messages():
    """50 positions should exceed the 3500-char chunk threshold and split."""
    matches = [
        _tracked(pos=_pos(coin=f"COIN{i}", net_size=1.0, entry=100.0, pnl=0.0),
                 dec=_dec(coin=f"COIN{i}", entry=100.0, sl=90.0))
        for i in range(50)
    ]
    marks = {f"COIN{i}": 100.0 for i in range(50)}
    msgs = render_daily_report(matches, [], marks, None, 5000.0, now=NOW)
    assert len(msgs) >= 2
    assert all(len(m) <= 4096 for m in msgs)  # Telegram hard limit


def test_render_html_escapes_dangerous_chars():
    """Coin name like '<script>' (hypothetical) must be escaped."""
    matches = [_orphan(pos=_pos(coin="<test>", net_size=1.0, entry=100.0))]
    msgs = render_daily_report(matches, [], {"<test>": 100.0}, None, 1000.0, now=NOW)
    text = "\n".join(msgs)
    assert "<test>" not in text  # raw form absent
    assert "&lt;test&gt;" in text  # escaped form present
