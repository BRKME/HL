"""Портфельная строка KPI: на малом счёте (пауза) не выпячивать проценты.

Оператор: счёт ~\$7, проект на паузе. 'неделя +1.7%' от \$7 = центы, поданные
как осмысленный процент — ложный сигнал. Ниже порога показываем режим паузы
без процентов P&L. Тактика и советник считаются как обычно (их выборка не
зависит от размера счёта)."""
from src.weekly_kpi import format_portfolio_line, MIN_ACCOUNT_USD


def test_tiny_account_marks_paused_no_percent():
    line = format_portfolio_line(pnl=1.0, roi=1.7, account_value=7.0)
    assert "пауза" in line.lower() or "малый счёт" in line.lower()
    assert "1.7%" not in line          # процент не выпячиваем
    assert "$7" in line


def test_normal_account_shows_full():
    line = format_portfolio_line(pnl=-545.0, roi=-25.2, account_value=1618.0)
    assert "-25.2%" in line
    assert "1,618" in line or "1618" in line


def test_threshold_boundary():
    # ровно на пороге считается нормальным
    line = format_portfolio_line(pnl=0.0, roi=0.0, account_value=MIN_ACCOUNT_USD)
    assert "пауза" not in line.lower()
