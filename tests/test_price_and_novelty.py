"""Точность цен и отметка новизны сигнала (30.08.2026, запрос оператора).

**Цены.** `NEAR $2` при реальной цене около $1.88. Формат округлял всё в
диапазоне 1–1000 до целого: для BTC это нормально, для дешёвых монет —
нет. Четыре монеты из девяти показывались с бесполезной точностью, а при
стопе в 1.6% ошибка округления больше всего риска сделки. Письмо для них
было неисполнимо, несмотря на добавленный стоп.

Правило: значащая точность, а не фиксированная — цена должна позволять
проверить, по той ли она входит.

**Новизна.** Читая ежедневную сводку, оператор не мог понять: `NEAR LONG` —
это сегодняшний сигнал или он висит третий день? От этого зависит всё
поведение: новый сигнал — повод действовать, тот же самый третий день —
повод не действовать, его уже видели и решение приняли. Без отметки
приходилось держать вчерашнее письмо в голове.
"""
from datetime import date

import pytest

from src.digest_history import mark_novelty
from src.whitelist_focus import _fmt_price


# ------------------------------------------------------------ цены

@pytest.mark.parametrize("price,expected", [
    (78007.0, "78 007"),      # BTC — целые, дробь не нужна
    (2454.0, "2 454"),
    (837.5, "837.5"),
    (237.4, "237.4"),
    (83.12, "83.12"),
    (1.8823, "1.882"),        # NEAR — раньше было «2»
    (2.4471, "2.447"),        # MORPHO — раньше «2»
    (0.6978, "0.6978"),
    (0.3530, "0.353"),
])
def test_price_keeps_significant_digits(price, expected):
    assert _fmt_price(price) == expected


def test_cheap_coin_precision_is_enough_for_a_stop():
    """Стоп 1.6% от 1.88 — это 0.03; цена обязана различать такие шаги."""
    a, b = _fmt_price(1.8823), _fmt_price(1.8523)
    assert a != b


def test_degenerate_prices():
    assert _fmt_price(0) == "—"
    assert _fmt_price(None) == "—"


# --------------------------------------------------------- новизна

def _v(coin, verdict):
    return (coin, 1.0, verdict, "тренд вверх.", verdict, "тренд вверх.", None)


def test_new_signal_is_marked():
    prev = {}
    marked, _ = mark_novelty([_v("NEAR", "LONG")], prev, date(2026, 8, 30))
    assert marked["NEAR"] == "🆕"


def test_repeated_signal_counts_days():
    prev = {"NEAR": {"verdict": "LONG", "since": "2026-08-28"}}
    marked, _ = mark_novelty([_v("NEAR", "LONG")], prev, date(2026, 8, 30))
    assert marked["NEAR"] == "3-й день"


def test_second_day_reads_naturally():
    prev = {"NEAR": {"verdict": "LONG", "since": "2026-08-29"}}
    marked, _ = mark_novelty([_v("NEAR", "LONG")], prev, date(2026, 8, 30))
    assert marked["NEAR"] == "2-й день"


def test_direction_change_resets_to_new():
    prev = {"TAO": {"verdict": "SHORT", "since": "2026-08-20"}}
    marked, _ = mark_novelty([_v("TAO", "LONG")], prev, date(2026, 8, 30))
    assert marked["TAO"] == "🆕"


def test_wait_is_not_marked():
    marked, _ = mark_novelty([_v("BTC", "WAIT")], {}, date(2026, 8, 30))
    assert "BTC" not in marked


def test_state_carries_forward_only_entries():
    prev = {"NEAR": {"verdict": "LONG", "since": "2026-08-28"}}
    _, new_state = mark_novelty(
        [_v("NEAR", "LONG"), _v("BTC", "WAIT")], prev, date(2026, 8, 30))
    assert new_state["NEAR"]["since"] == "2026-08-28"
    assert "BTC" not in new_state


def test_dropped_signal_disappears_from_state():
    prev = {"TAO": {"verdict": "LONG", "since": "2026-08-25"}}
    _, new_state = mark_novelty([_v("NEAR", "LONG")], prev, date(2026, 8, 30))
    assert "TAO" not in new_state


def test_corrupt_previous_state_is_survivable():
    marked, _ = mark_novelty([_v("NEAR", "LONG")],
                             {"NEAR": "мусор"}, date(2026, 8, 30))
    assert marked["NEAR"] == "🆕"
