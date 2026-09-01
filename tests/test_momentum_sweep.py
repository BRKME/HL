"""Перебор моделей моментума с честным разделением выборки (01.09.2026).

Оператор справедливо возразил против преждевременного вывода: движок у нас
есть, тысячи прогонов стоят минуты, а известные рабочие подходы мы не
перебрали.

Из внешнего обзора взято четыре проверяемых утверждения: временнáя
моментум-стратегия сильнее кросс-секционной при высокой корреляции; лучшее
окно около 28 дней при удержании около 5; доходность BTC выше ПОСЛЕ
локальных максимумов (наш фильтр перегрева отсекает ровно это);
волатильностная нормировка улучшает результат.

Главная опасность здесь — подгонка: тысяча вариантов на одних данных даст
победителя и на чистом шуме. Поэтому история делится 70/30, лучший
выбирается ТОЛЬКО по обучению, а судят по проверке, которую он не видел.
"""
import pytest

from src.momentum_sweep import (
    Params, default_grid, evaluate, run_one, split, sweep,
)


def _trend(n=400, base=100.0, drift=0.003):
    out, p = [], base
    for i in range(n):
        p *= (1 + drift + (0.02 if i % 11 == 0 else -0.004))
        out.append(p)
    return out


def _noise(n=400, base=100.0, seed=5):
    import random
    rng = random.Random(seed)
    out, p = [], base
    for _ in range(n):
        p *= (1 + rng.uniform(-0.03, 0.03))
        out.append(p)
    return out


def test_split_is_seventy_thirty():
    train, test = split(list(range(100)))
    assert len(train) == 70 and len(test) == 30
    assert train[-1] < test[0]


def test_momentum_profits_on_a_trend():
    r = evaluate(_trend(), Params(28, 5, False, True))
    assert r.n > 0
    assert r.avg_r > 0


def test_momentum_does_not_profit_on_noise():
    """На чистом шуме преимущества быть не должно — иначе модель врёт."""
    r = evaluate(_noise(), Params(28, 5, False, False))
    if r.n > 5:
        assert abs(r.avg_r) < 0.5


def test_vol_scaling_reduces_trade_count():
    """Нормировка отсеивает сигналы слабее собственного шума."""
    plain = evaluate(_noise(), Params(28, 5, False, False))
    scaled = evaluate(_noise(), Params(28, 5, True, False))
    assert scaled.n <= plain.n


def test_long_only_takes_no_shorts():
    down = [100.0 * (0.99 ** i) for i in range(400)]
    assert evaluate(down, Params(28, 5, False, True)).n == 0


def test_grid_is_large_enough_to_matter():
    grid = default_grid()
    assert len(grid) >= 100
    assert Params(28, 5, False, False) in grid


def test_sweep_reports_train_and_test_separately():
    res = sweep({"A": _trend(), "B": _noise()}, default_grid()[:6])
    assert res and len(res[0]) == 3
    for p, tr, te in res:
        assert tr.params == p and te.params == p


def test_empty_series_is_safe():
    assert run_one([], Params(28, 5, False, False)) == []
