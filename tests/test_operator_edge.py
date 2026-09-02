"""Разбор торговли оператора (02.09.2026).

Два дня мы доказывали, что модель не обгоняет случайный вход. За тот же
период оператор сделал руками +46.8%, и его результат не измерялся ни
разу — притом что это единственный источник преимущества, который мы
наблюдали.

Мерить надо тем же инструментом, иначе выйдет двойной стандарт: к модели
строгие требования, к оператору никаких. В том числе тот же порог по
числу сделок: меньше тридцати — вывод невозможен, и это говорится прямо,
а не заменяется вдохновляющей формулировкой.
"""
import pytest

from src.operator_edge import (
    MIN_TRADES_FOR_ANY_CLAIM, OperatorTrade, pair_fills, summarise, verdict,
)

DAY = 86_400_000


def _fill(coin, px, sz, side, t):
    return {"coin": coin, "px": str(px), "sz": str(sz), "dir": side,
            "time": t, "closedPnl": "0"}


def test_open_and_close_make_one_trade():
    fills = [_fill("ETH", 100, 1, "Open Long", 0),
             _fill("ETH", 110, 1, "Close Long", DAY)]
    trades = pair_fills(fills)
    assert len(trades) == 1
    assert trades[0].direction == "LONG"
    assert trades[0].return_pct == pytest.approx(10.0)


def test_short_return_is_inverted():
    fills = [_fill("ETH", 100, 1, "Open Short", 0),
             _fill("ETH", 90, 1, "Close Short", DAY)]
    assert pair_fills(fills)[0].return_pct == pytest.approx(10.0)


def test_partial_closes_are_one_trade():
    """Позиция набирается и закрывается частями — это одна сделка."""
    fills = [_fill("ETH", 100, 2, "Open Long", 0),
             _fill("ETH", 105, 1, "Close Long", DAY),
             _fill("ETH", 110, 1, "Close Long", 2 * DAY)]
    assert len(pair_fills(fills)) == 1


def test_open_position_is_not_counted():
    """Незакрытая сделка не имеет результата — считать её нельзя."""
    assert pair_fills([_fill("ETH", 100, 1, "Open Long", 0)]) == []


def test_hold_days_measured():
    fills = [_fill("ETH", 100, 1, "Open Long", 0),
             _fill("ETH", 110, 1, "Close Long", 3 * DAY)]
    assert pair_fills(fills)[0].hold_days == pytest.approx(3.0)


def test_close_without_open_is_ignored():
    assert pair_fills([_fill("ETH", 100, 1, "Close Long", DAY)]) == []


def test_summary_shapes():
    t = OperatorTrade("ETH", "LONG", 0, DAY, 100, 110, 1, 10.0)
    s = summarise([t, OperatorTrade("BTC", "LONG", 0, DAY, 100, 95, 1, -5.0)])
    assert s["n"] == 2
    assert s["wr"] == pytest.approx(0.5)
    assert s["total_pnl_usd"] == pytest.approx(5.0)
    assert set(s["by_coin"]) == {"ETH", "BTC"}


def test_empty_summary():
    assert summarise([])["n"] == 0


def test_verdict_refuses_small_samples():
    """Тот же порог, что применялся к модели — без поблажек."""
    s = summarise([OperatorTrade("ETH", "LONG", 0, DAY, 100, 110, 1, 10.0)])
    assert "НЕЛЬЗЯ СУДИТЬ" in verdict(s)


def test_verdict_needs_control_even_when_positive():
    """Положительный результат сам по себе выводом не является."""
    trades = [OperatorTrade("ETH", "LONG", 0, DAY, 100, 110, 1, 10.0)
              for _ in range(MIN_TRADES_FOR_ANY_CLAIM)]
    assert "контроль" in verdict(summarise(trades))


def test_verdict_calls_out_negative_result():
    trades = [OperatorTrade("ETH", "LONG", 0, DAY, 100, 90, 1, -10.0)
              for _ in range(MIN_TRADES_FOR_ANY_CLAIM)]
    assert "преимущества нет" in verdict(summarise(trades))
