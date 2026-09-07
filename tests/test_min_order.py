"""Исполнимость размера позиции (07.09.2026).

Проверка дайджеста от 07.09 при счёте $174: все предложенные размеры
оказались НИЖЕ минимального ордера биржи.

  HYPE   размер 1.4% = $2.44 нотионала
  NEAR   размер 1.0% = $1.74
  BTC    размер 2.5% = $4.35

Минимальный ордер на Hyperliquid около $10 — ни одну из этих сделок
открыть нельзя. В процентах это невидимо: «размер ~1.0%» выглядит
нормально, пока не переведёшь в деньги.

Причина: размер считается от риска 1% депозита ($1.74), делится на число
одновременных входов, и при семи входах остаются центы. Логика верна, но
результат неисполним — тот же класс, что стоп не на своей стороне.
"""
import pytest

from src.whitelist_focus import MIN_ORDER_USD, _plan_line


def test_shows_size_in_dollars():
    out = _plan_line("LONG", entry=100.0, sl=90.0, n_entries=1, equity=1000.0)
    assert "$" in out


def test_flags_size_below_minimum():
    """Ровно случай 07.09: процент выглядит нормально, деньги — нет."""
    out = _plan_line("LONG", entry=100.0, sl=86.0, n_entries=7, equity=174.0)
    assert "ниже минимума" in out


def test_no_flag_when_executable():
    out = _plan_line("LONG", entry=100.0, sl=90.0, n_entries=1, equity=5000.0)
    assert "ниже минимума" not in out


def test_without_equity_shows_percent_only():
    """Старые вызовы не должны сломаться."""
    out = _plan_line("LONG", entry=100.0, sl=90.0, n_entries=1)
    assert "размер" in out
    assert "$" not in out.split("размер")[1]


def test_minimum_is_explicit():
    assert MIN_ORDER_USD == 10.0


def test_dividing_between_entries_can_break_executability():
    """Деление размера между входами — правильная мера против бета-ставки,
    но при малом счёте оно делает каждую сделку неисполнимой."""
    one = _plan_line("LONG", 100.0, 90.0, n_entries=1, equity=174.0)
    seven = _plan_line("LONG", 100.0, 90.0, n_entries=7, equity=174.0)
    assert "ниже минимума" not in one
    assert "ниже минимума" in seven
