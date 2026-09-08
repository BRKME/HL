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
    """Пометка нужна там, где сделка неисполнима даже без деления.

    После правки 07.09 размер делится не на все входы, а на то, сколько
    счёт тянет, — поэтому семь входов при $174 больше не дают центы.
    Неисполнимость остаётся при совсем крошечном счёте."""
    out = _plan_line("LONG", entry=100.0, sl=90.0, n_entries=1, equity=50.0)
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


def test_division_no_longer_breaks_executability():
    """Было: деление на семь входов при $174 давало по $2 — сделку
    открыть нельзя. Стало: делим на то, сколько счёт тянет, и обе
    сделки исполнимы. Мера против бета-ставки сохранена отдельной
    строкой «счёт тянет N из M»."""
    one = _plan_line("LONG", 100.0, 90.0, n_entries=1, equity=174.0)
    seven = _plan_line("LONG", 100.0, 90.0, n_entries=7, equity=174.0)
    assert "ниже минимума" not in one
    assert "ниже минимума" not in seven


# ------------------------- сколько сделок тянет счёт (решение 07.09)

def test_supportable_entries_small_account():
    """При $174 и стопе 10% счёт тянет ОДНУ сделку, а дайджест делил на 7."""
    from src.whitelist_focus import supportable_entries

    assert supportable_entries(174, stop_pct=10) == 1
    assert supportable_entries(174, stop_pct=6) == 2


def test_supportable_entries_grows_with_capital():
    from src.whitelist_focus import supportable_entries

    assert supportable_entries(1000, 10) == 10
    assert supportable_entries(5000, 10) == 50


def test_supportable_entries_degenerate():
    from src.whitelist_focus import supportable_entries

    assert supportable_entries(None, 10) == 0
    assert supportable_entries(174, 0) == 0


def test_size_divided_by_supportable_not_by_all():
    """Ключевая правка: делим на то, что счёт тянет, а не на все входы —
    иначе мера против бета-ставки делает каждую сделку неисполнимой."""
    seven = _plan_line("LONG", 100.0, 90.0, n_entries=7, equity=174.0)
    assert "ниже минимума" not in seven


def test_large_account_still_divides_by_all_entries():
    """На большом счёте ограничение не действует — делим как раньше."""
    import re

    one = _plan_line("LONG", 100.0, 90.0, n_entries=1, equity=100000.0)
    seven = _plan_line("LONG", 100.0, 90.0, n_entries=7, equity=100000.0)

    def pct(x):
        m = re.search(r"размер ~([\d.]+)%", x)
        return float(m.group(1)) if m else None

    assert pct(seven) < pct(one)
