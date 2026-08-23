"""Страж достоверности ROI в KPI и утреннее окно дайджеста (23.08.2026).

**ROI.** Отчёт с 03.08 не печатает процент, если счёт двигали вводами или
выводами: `roi = pnl / start_value` осмыслен, только когда start_value —
тот капитал, который заработал этот pnl. В KPI тот же страж не стоял, хотя
строка выглядит так же и читается так же. Сегодня цифры совпали случайно
(net_flow ≈ 0), но одно пополнение — и KPI покажет процент, который отчёт
в тот же день скроет.

**Окно.** Дайджест приходил в 12–13 МСК вместо задуманных 10:00: cron
стартовал в 07:00 UTC, а Actions доставляет с задержкой 1–3 часа, и первый
тик дня падал в 09–10 UTC. Старт сдвинут на 05:00 UTC, нижняя граница окна
— на 06:00 UTC, чтобы ранняя доставка не пролетала мимо.
"""
from datetime import datetime, timezone

import pytest

from src.morning_gate import EARLIEST_UTC_HOUR, should_run_digest
from src.portfolio_performance import PeriodStats
from src.weekly_kpi import format_portfolio_line


def _t(hour, day=23):
    return datetime(2026, 8, day, hour, 0, tzinfo=timezone.utc)


def _ps(pnl, start, end):
    return PeriodStats(period="week", pnl=pnl, start_value=start,
                       end_value=end, vlm=0.0,
                       roi_pct=(pnl / start * 100) if start > 0 else 0.0)


# --------------------------------------------------------- ROI в KPI

def test_clean_week_shows_percent():
    """Ровно неделя 23.08: +$63 на базе ~$126, вводов не было."""
    out = format_portfolio_line(63.0, 49.8, 189.0, period=_ps(63.0, 126.5, 189.0))
    assert "+49.8%" in out
    assert "+63$" in out


def test_deposit_hides_percent_but_keeps_money():
    out = format_portfolio_line(-42.0, -703.2, 62.0, period=_ps(-42.0, 6.0, 62.0))
    assert "703" not in out
    assert "%" not in out
    assert "-42$" in out


def test_withdrawal_hides_percent():
    out = format_portfolio_line(-17.0, -10.3, 62.0, period=_ps(-17.0, 165.0, 62.0))
    assert "%" not in out


def test_without_period_falls_back_to_old_behaviour():
    """Старые вызовы без period не должны падать."""
    out = format_portfolio_line(10.0, 5.0, 200.0)
    assert "+5.0%" in out


def test_small_account_still_pauses_percentages():
    out = format_portfolio_line(1.0, 17.0, 7.0, period=_ps(1.0, 6.0, 7.0))
    assert "малый счёт" in out


# ------------------------------------------------------ окно дайджеста

def test_window_opens_at_six_utc():
    assert EARLIEST_UTC_HOUR == 6


def test_early_delivery_is_caught(tmp_path):
    """Cron 05:00 UTC, доставка с задержкой в 06:20 — раньше пролетало."""
    assert should_run_digest(_t(6), tmp_path) is True


def test_predawn_run_still_skipped(tmp_path):
    assert should_run_digest(_t(4), tmp_path) is False


def test_late_delivery_still_caught(tmp_path):
    assert should_run_digest(_t(10), tmp_path) is True


def test_evening_still_skipped(tmp_path):
    assert should_run_digest(_t(20), tmp_path) is False
