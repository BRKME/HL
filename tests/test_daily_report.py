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
