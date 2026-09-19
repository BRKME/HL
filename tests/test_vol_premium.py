"""Премия за волатильность (19.09.2026, второй этап).

Отвечает на единственный вопрос политики по опционам: какая из двух премий
перевешивает — за риск актива (в пользу покупателя) или за волатильность
(в пользу продавца).

Реализованная считается оценкой Паркинсона: по максимумам и минимумам, а
не по закрытиям. День, сходивший на 5% вверх и вернувшийся, при оценке по
закрытиям засчитывается как нулевой — а наблюдений у нас мало, и терять их
нельзя.

Подразумеваемая берётся по СЕРЕДИНЕ стакана: на Aevo разрыв достигает 9.9
пункта по биткоину, и выбор любой стороны сдвинул бы замер на половину
спреда.
"""
import math

import pytest

from src.vol_premium import (
    MIN_OBSERVATIONS, PremiumPoint, atm_implied, close_to_close_volatility,
    parkinson_volatility, summarise,
)


def _flat(n=60, price=100.0):
    return [{"h": price, "l": price, "c": price} for _ in range(n)]


def _swinging(n=60, price=100.0, daily=0.04):
    return [{"h": price * (1 + daily), "l": price * (1 - daily), "c": price}
            for _ in range(n)]


# ------------------------------------------------ реализованная волатильность

def test_flat_market_has_zero_volatility():
    assert parkinson_volatility(_flat()) == pytest.approx(0.0, abs=1e-9)


def test_swinging_market_has_high_volatility():
    v = parkinson_volatility(_swinging())
    assert v > 0.5


def test_parkinson_catches_what_closes_miss():
    """Ключевое: день сходил вверх и вернулся. По закрытиям — ноль
    движения, по максимумам и минимумам — настоящая волатильность."""
    swings = _swinging(daily=0.05)
    assert parkinson_volatility(swings) > 0.5
    assert close_to_close_volatility(swings) == pytest.approx(0.0, abs=1e-9)


def test_too_few_observations_returns_none():
    """Оценка по трём дням — не оценка, и ноль тут был бы ложью."""
    assert parkinson_volatility(_flat(n=MIN_OBSERVATIONS - 1)) is None
    assert close_to_close_volatility(_flat(n=5)) is None


def test_broken_candles_are_skipped():
    rows = _swinging(n=40) + [None, {"h": 0, "l": 0}, {"c": 1}]
    assert parkinson_volatility(rows) is not None


# ----------------------------------------- подразумеваемая из стакана

def test_implied_is_mid_of_spread():
    """Спред на Aevo до 9.9 пункта — любая сторона сдвинула бы замер."""
    bid = [["7347.5", "1.5", "0.296761"]]
    ask = [["8152.5", "1.5", "0.395666"]]
    assert atm_implied(bid, ask) == pytest.approx(0.3462, abs=1e-3)


def test_implied_from_one_side_when_other_empty():
    assert atm_implied([["1", "1", "0.4"]], []) == pytest.approx(0.4)


def test_implied_none_without_iv_field():
    assert atm_implied([["1", "1"]], [["2", "1"]]) is None


def test_implied_skips_zero_iv():
    assert atm_implied([["1", "1", "0"], ["1", "1", "0.5"]], []) == 0.5


# --------------------------------------------------------------- премия

def test_premium_is_in_percentage_points():
    p = PremiumPoint(0, "BTC", implied=0.40, realized=0.30, horizon_days=30)
    assert p.premium_pp == pytest.approx(10.0)


def test_negative_premium_when_realized_exceeds_implied():
    """В январе 2026 премия уходила глубоко в минус — замер обязан это
    показывать, а не считать аномалией."""
    p = PremiumPoint(0, "BTC", implied=0.30, realized=0.55, horizon_days=30)
    assert p.premium_pp < 0


def test_summary_reports_worst_not_only_mean():
    """Среднее прячет хвост: продавец волатильности теряет крупно и редко."""
    pts = [PremiumPoint(0, "BTC", 0.4, 0.3, 30),
           PremiumPoint(0, "BTC", 0.3, 0.9, 30)]
    s = summarise(pts)
    assert s["worst_pp"] < -50
    assert s["positive_share"] == pytest.approx(0.5)


def test_empty_summary_is_not_zero():
    s = summarise([])
    assert s["n"] == 0
    assert s["mean_pp"] is None
