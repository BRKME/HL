"""Regression tests — Telegram report defects spotted 03.08.2026.

Defect 1: «Месяц: -$42 (-703.2%)». roi_pct = pnl / start_value, and
start_value is the account value at the *start* of the window. When the
wallet is funded or drained mid-period that base is not the capital that
earned the PnL, so the percentage is meaningless (month base was ~$6).

Defect 2: «✅ Без алертов, всё спокойно» printed in the same message as two
«🔴 ЗАКРОЙ ПО ПОЛИТИКЕ» lines — the calm line was decided before pending
exits were read from the journal.
"""
from datetime import datetime, timezone

from src.daily_report import _render_performance, render_daily_report
from src.portfolio_performance import (
    PeriodStats,
    PerformanceSnapshot,
    roi_is_reliable,
)


def _ps(period, pnl, start, end):
    roi = (pnl / start * 100) if start > 0 else 0.0
    return PeriodStats(period=period, pnl=pnl, start_value=start,
                       end_value=end, vlm=0.0, roi_pct=roi)


# ------------------------------------------------------------ roi_is_reliable

def test_roi_reliable_when_no_flows():
    # $100 -> $110 with +$10 pnl: change fully explained by pnl
    assert roi_is_reliable(_ps("day", 10.0, 100.0, 110.0)) is True


def test_roi_unreliable_on_deposit():
    # base $6, ended $62, pnl -$42 => ~$98 deposited (the -703% case)
    assert roi_is_reliable(_ps("month", -42.0, 6.0, 62.0)) is False


def test_roi_unreliable_on_withdrawal():
    # $165 -> $62 with only -$17 pnl => ~$86 withdrawn
    assert roi_is_reliable(_ps("week", -17.0, 165.0, 62.0)) is False


def test_roi_unreliable_when_start_value_zero():
    assert roi_is_reliable(_ps("day", 5.0, 0.0, 5.0)) is False


def test_roi_unreliable_on_absurd_magnitude():
    # belt-and-braces: even if flows look clean, >200% on a period is a bug
    assert roi_is_reliable(_ps("day", 500.0, 10.0, 510.0)) is False


def test_small_flow_tolerated():
    # $1 of fees/dust on a $100 base should not kill the percentage
    assert roi_is_reliable(_ps("day", 10.0, 100.0, 111.0)) is True


# ------------------------------------------------- _render_performance output

def _snap(day, week, month):
    return PerformanceSnapshot(
        address="combined", day=day, week=week, month=month,
        all_time=_ps("allTime", 0.0, 0.0, 0.0),
        current_account_value=62.0,
    )


def test_absurd_roi_not_rendered():
    out = _render_performance(_snap(
        _ps("day", 6.0, 140.0, 62.0),
        _ps("week", -17.0, 165.0, 62.0),
        _ps("month", -42.0, 6.0, 62.0),
    ))
    assert "703" not in out
    assert "%" not in out          # all three periods have flows here
    assert "(" not in out.split("Доходность")[1]
    assert "-$42" in out           # money is still shown


def test_clean_roi_still_rendered():
    out = _render_performance(_snap(
        _ps("day", 10.0, 100.0, 110.0),
        _ps("week", -5.0, 105.0, 100.0),
        _ps("month", 20.0, 90.0, 110.0),
    ))
    assert "+10.0%" in out
    assert "+$10" in out


# ------------------------------------------------------ calm line suppression

def _minimal_report(**kw):
    return render_daily_report(
        matches=[], alerts=[], marks={}, current_snapshot=None,
        total_account_value=62.0,
        now=datetime(2026, 8, 3, 17, 8, tzinfo=timezone.utc),
        **kw,
    )


def test_calm_line_absent_when_exits_pending(monkeypatch, tmp_path):
    """With unexecuted exits in the journal the report must not claim calm."""
    import src.daily_report as dr

    monkeypatch.setattr(dr, "pending_exits",
                        lambda rows: {"NEAR": {"reason": "verdict_flip"}})
    text = "\n".join(_minimal_report(spot=[]))
    assert "Без алертов" not in text
