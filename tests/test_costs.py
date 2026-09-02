"""Издержки в бэктесте (02.09.2026).

Их не было вовсе. Пока преимущество было отрицательным, это не мешало:
не работает без издержек — с ними тем более. Но измеренное преимущество
против случайного входа составляет +0.44…+0.78 R, а издержки при удержании
в 20 дней — около 0.24…0.30 R. Они съедают от трети до двух третей, то
есть решают знак.

Состав: комиссия дважды (вход и выход) плюс фандинг за время удержания.
Знак фандинга важен — лонг ПЛАТИТ при положительной ставке. Все наши
сигналы за месяц были лонгами при ставке +11%: система систематически
вставала на сторону, которая платит.
"""
import pytest

from src.momentum_sweep import Params, run_one, trade_cost_r


def _trend(n=400, base=100.0, drift=0.003):
    out, p = [], base
    for i in range(n):
        p *= (1 + drift + (0.02 if i % 11 == 0 else -0.004))
        out.append(p)
    return out


def test_cost_grows_with_holding():
    short = trade_cost_r(5, vol=0.04)
    long_ = trade_cost_r(20, vol=0.04)
    assert long_ > short > 0


def test_fee_is_charged_twice():
    """Вход и выход — две комиссии, даже при нулевом удержании."""
    from src.momentum_sweep import TAKER_FEE_PCT

    cost = trade_cost_r(0, vol=0.04, funding_apr=0.0)
    assert cost == pytest.approx(TAKER_FEE_PCT * 2 / 4.0, rel=1e-6)


def test_short_receives_funding_when_rate_positive():
    """Лонг платит, шорт получает — знак обязан различаться."""
    long_cost = trade_cost_r(20, vol=0.04, direction=1)
    short_cost = trade_cost_r(20, vol=0.04, direction=-1)
    assert long_cost > short_cost


def test_cost_scales_inversely_with_volatility():
    """На волатильной монете те же проценты стоят меньше R."""
    assert trade_cost_r(10, vol=0.02) > trade_cost_r(10, vol=0.08)


def test_zero_volatility_is_safe():
    assert trade_cost_r(10, vol=0.0) == 0.0


def test_net_run_is_worse_than_gross():
    p = Params(28, 20, False, True)
    gross = run_one(_trend(), p)
    net = run_one(_trend(), p, net_of_costs=True)
    assert len(gross) == len(net)
    assert sum(net) < sum(gross)


def test_costs_are_off_by_default():
    """Старые вызовы обязаны считать по-прежнему — иначе прежние прогоны
    станут несравнимы с новыми молча."""
    p = Params(28, 5, False, True)
    assert run_one(_trend(), p) == run_one(_trend(), p, net_of_costs=False)


def test_long_holding_costs_more_than_short_holding():
    long_p = Params(28, 20, False, True)
    short_p = Params(28, 1, False, True)
    c_long = trade_cost_r(20, 0.04)
    c_short = trade_cost_r(1, 0.04)
    assert c_long > c_short * 3
