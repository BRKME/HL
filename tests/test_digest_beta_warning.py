"""Предупреждение о бета-ставке в дайджесте (24.08.2026).

Дайджест 24.08 выдал четыре LONG за один проход. Тактический слой в таком
случае предупреждает («несколько монет в одну сторону — это одна бета-ставка
на рынок»), а дайджест — нет, хотя показывает ту же картину сразу по девяти
монетам и читается как четыре независимые идеи.

На счёте в $189 четыре лонга по «тактическому размеру» 8–12% каждый — это
треть депозита в одну сторону при корреляции альтов, близкой к единице.
"""
from src.digest_compact import beta_warning


def _v(coin, verdict):
    return (coin, 1.0, verdict, "тренд вверх.", verdict, "тренд вверх.")


def test_four_longs_trigger_warning():
    out = beta_warning([_v("NEAR", "LONG"), _v("ASTER", "LONG"),
                        _v("TAO", "LONG"), _v("ONDO", "LONG")])
    assert out
    assert "4" in out


def test_two_longs_trigger_warning():
    out = beta_warning([_v("NEAR", "LONG"), _v("TAO", "LONG")])
    assert out


def test_single_long_is_silent():
    assert beta_warning([_v("NEAR", "LONG"), _v("TAO", "WAIT")]) == ""


def test_mixed_directions_are_not_one_bet():
    """Лонг и шорт одновременно — не одна ставка, а хедж."""
    assert beta_warning([_v("NEAR", "LONG"), _v("TAO", "SHORT")]) == ""


def test_all_wait_is_silent():
    assert beta_warning([_v("NEAR", "WAIT"), _v("TAO", "WAIT")]) == ""


def test_multiple_shorts_also_warn():
    out = beta_warning([_v("NEAR", "SHORT"), _v("TAO", "SHORT")])
    assert out
    assert "SHORT" in out


def test_empty_input():
    assert beta_warning([]) == ""
