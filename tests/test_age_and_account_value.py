"""Возраст требования и единый источник счёта (09.08.2026).

**Возраст.** Сообщение 09.08 14:28 показывало «exit 07.08 20:08 (1 дн
назад)». Прошло 42.3 часа — почти двое суток. Округление вниз через
`delta.days` систематически занижает застой почти на сутки, а весь смысл
показа возраста в том, чтобы застой было видно.

**Счёт.** В одном канале с разницей в шесть минут:

    12:32  KPI:     счёт $143
    12:38  Отчёт:   $130

Разные источники одного числа. Отчёт берёт живое `accountValue` из
clearinghouse, KPI — последнюю точку суточного ряда истории портфеля,
которая сэмплируется и отстаёт. Два числа для одного и того же — то же
семейство, что «-703.2%» и «Всего сигналов: 146».
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.daily_report import _fmt_age

NOW = datetime(2026, 8, 9, 11, 28, tzinfo=timezone.utc)


def _ago(hours):
    return (NOW - timedelta(hours=hours)).isoformat()


# ------------------------------------------------------------ возраст

def test_hours_shown_below_two_days():
    assert "ч назад" in _fmt_age(_ago(5), NOW)


def test_forty_two_hours_is_not_one_day():
    """Ровно случай 09.08: 42 часа читались как «1 дн»."""
    out = _fmt_age(_ago(42), NOW)
    assert "1 дн" not in out
    assert "42 ч" in out


def test_two_days_rounds_up_not_down():
    assert "2 дн" in _fmt_age(_ago(60), NOW)


def test_exactly_two_days():
    assert "2 дн" in _fmt_age(_ago(48), NOW)


def test_thirteen_days_still_reads_in_days():
    assert "13 дн" in _fmt_age(_ago(24 * 13), NOW)


def test_rounding_does_not_understate():
    """Ни при каком возрасте показанное не меньше фактического более чем
    на полшага — занижение застоя опаснее завышения."""
    for hours in (25, 35, 47, 49, 71, 95, 300):
        out = _fmt_age(_ago(hours), NOW)
        shown = float(out.split("(")[1].split()[0])
        actual = hours if "ч" in out else hours / 24
        assert shown >= actual - 0.5


def test_future_timestamp_yields_nothing():
    assert _fmt_age((NOW + timedelta(hours=3)).isoformat(), NOW) == ""


def test_garbage_timestamp_yields_nothing():
    assert _fmt_age("не дата", NOW) == ""
    assert _fmt_age("", NOW) == ""


def test_missing_now_yields_nothing():
    assert _fmt_age(_ago(10), None) == ""


# -------------------------------------------------------------- счёт

def test_kpi_prefers_live_account_value(monkeypatch):
    """KPI и отчёт обязаны называть одно число одинаково."""
    import src.weekly_kpi as kpi

    monkeypatch.setattr(kpi, "_live_account_value", lambda accounts: 130.0,
                        raising=False)
    line = kpi.format_portfolio_line(-2.0, -1.2, 130.0)
    assert "130" in line
    assert "143" not in line


def test_live_value_falls_back_when_unavailable(monkeypatch):
    """Живое значение недоступно — берём историю, но не молчим."""
    import src.weekly_kpi as kpi

    def boom(accounts):
        raise RuntimeError("HL недоступен")

    monkeypatch.setattr(kpi, "_live_account_value", boom, raising=False)
    assert kpi._account_value_or_fallback([], fallback=143.0) == 143.0
