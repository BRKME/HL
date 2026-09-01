"""Проверка H4: даёт ли относительная сила преимущество (01.09.2026).

Гипотеза зарегистрирована 24.08 до данных. Наблюдение: фильтр перегрева
систематически отсеивает лидеров — медиана RS у заблокированных +7.5, у
прошедших 0.0 на 954 записях. Отсюда предположение, что RS предсказывает,
а фильтр перегрева вредит.

Проверяется тем же бэктестером и на тех же входах: сделки размечаются по
относительной силе монеты против BTC на момент входа, затем сравнивается
средний R у сильных и у слабых. Если RS предсказывает, разница должна
быть заметной и в правильную сторону.

Это последний кандидат на преимущество, который у нас есть. Четыре
варианта выхода уже показали, что дело не в них.
"""
import pytest

from src.tactical_backtest import rs_at, split_by_rs


def _series(base, drift, n=260):
    out, p = [], base
    for _ in range(n):
        p *= (1 + drift)
        out.append({"o": p, "h": p * 1.01, "l": p * 0.99, "c": p})
    return out


def test_rs_positive_when_coin_outperforms():
    coin = _series(100, 0.004)
    btc = _series(100, 0.001)
    assert rs_at(coin, btc, idx=250, lookback=30) > 0


def test_rs_negative_when_coin_lags():
    coin = _series(100, 0.0005)
    btc = _series(100, 0.003)
    assert rs_at(coin, btc, idx=250, lookback=30) < 0


def test_rs_none_without_history():
    assert rs_at(_series(100, 0.002, n=10), _series(100, 0.002, n=10),
                 idx=5, lookback=30) is None


def test_rs_none_when_btc_missing():
    assert rs_at(_series(100, 0.002), None, idx=250, lookback=30) is None


def test_split_separates_strong_and_weak():
    class T:
        def __init__(self, r, rs):
            self.r, self.rs = r, rs

    trades = [T(0.5, 10.0), T(-0.2, -5.0), T(0.3, 8.0), T(-0.4, -12.0)]
    strong, weak = split_by_rs(trades)
    assert len(strong) == 2 and len(weak) == 2
    assert all(t.rs > 0 for t in strong)


def test_split_ignores_trades_without_rs():
    class T:
        def __init__(self, r, rs):
            self.r, self.rs = r, rs

    strong, weak = split_by_rs([T(0.1, None), T(0.2, 5.0)])
    assert len(strong) == 1 and len(weak) == 0
