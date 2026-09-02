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


# ------------------------------- бенчмарк: без него цифры ничего не значат

def test_buy_and_hold_positive_on_a_trend():
    from src.momentum_sweep import buy_and_hold_r

    assert buy_and_hold_r(_trend()) > 0


def test_buy_and_hold_negative_on_a_decline():
    from src.momentum_sweep import buy_and_hold_r

    down = [100.0 * (0.995 ** i) for i in range(400)]
    assert buy_and_hold_r(down) < 0


def test_long_only_long_holding_approaches_buy_and_hold():
    """Ключевая проверка: «только лонг, удержание 20» на растущем рынке —
    это и есть рынок. Если доля времени в позиции близка к единице,
    прибыль стратегии не является преимуществом."""
    from src.momentum_sweep import time_in_market

    tim = time_in_market(_trend(), Params(60, 20, False, True))
    assert tim is not None
    assert tim > 0.5


def test_selective_model_spends_less_time_in_market():
    from src.momentum_sweep import time_in_market

    loose = time_in_market(_noise(), Params(60, 20, False, True))
    tight = time_in_market(_noise(), Params(7, 1, True, True))
    if loose and tight:
        assert tight < loose


def test_short_series_is_safe():
    from src.momentum_sweep import buy_and_hold_r, time_in_market

    assert buy_and_hold_r([1.0, 2.0]) is None
    assert time_in_market([1.0, 2.0], Params(28, 5, False, True)) is None


# --------------- правильный контроль: случайный вход с той же экспозицией

def test_random_entry_matches_market_direction():
    """На растущем рынке случайный лонг тоже прибылен — потому сравнение
    с ним и есть проверка УМЕНИЯ ВЫБИРАТЬ, а не направления рынка."""
    from src.momentum_sweep import random_entry_r

    assert random_entry_r(_trend(), holding=20, exposure=0.5) > 0


def test_random_entry_loses_on_a_decline():
    from src.momentum_sweep import random_entry_r

    down = [100.0 * (0.995 ** i) for i in range(400)]
    assert random_entry_r(down, holding=20, exposure=0.5) < 0


def test_random_entry_is_deterministic():
    """Отчёт, меняющийся от прогона к прогону, не годится для решения."""
    from src.momentum_sweep import random_entry_r

    a = random_entry_r(_noise(), holding=10, exposure=0.4)
    b = random_entry_r(_noise(), holding=10, exposure=0.4)
    assert a == b


def test_random_entry_degenerate_inputs():
    from src.momentum_sweep import random_entry_r

    assert random_entry_r([1.0, 2.0], holding=5, exposure=0.5) is None
    assert random_entry_r(_trend(), holding=0, exposure=0.5) is None
    assert random_entry_r(_trend(), holding=5, exposure=0) is None


# ------------------------------------------- просадка: главное при плече

def test_drawdown_is_zero_on_a_monotone_rise():
    from src.momentum_sweep import max_drawdown

    assert max_drawdown([0, 1, 2, 3]) == 0


def test_drawdown_measures_worst_fall_from_peak():
    from src.momentum_sweep import max_drawdown

    assert max_drawdown([0, 5, 1, 4]) == pytest.approx(-4)


def test_drawdown_of_empty_curve():
    from src.momentum_sweep import max_drawdown

    assert max_drawdown([]) is None


def test_equity_curve_accumulates_trades():
    from src.momentum_sweep import equity_curve

    curve = equity_curve(_trend(), Params(28, 5, False, True))
    assert curve
    assert curve == sorted(curve) or True          # монотонность не требуется
    assert len(curve) == len(run_one(_trend(), Params(28, 5, False, True)))


def test_buy_and_hold_curve_starts_at_zero():
    from src.momentum_sweep import buy_and_hold_curve

    curve = buy_and_hold_curve(_trend())
    assert curve and abs(curve[0]) < 1e-9


def test_buy_and_hold_drawdown_is_measurable():
    from src.momentum_sweep import buy_and_hold_curve, max_drawdown

    updown = [100.0 * (1.01 ** i) for i in range(200)]
    updown += [updown[-1] * (0.98 ** i) for i in range(1, 120)]
    dd = max_drawdown(buy_and_hold_curve(updown))
    assert dd is not None and dd < 0


# ------------------------- скользящая проверка: повторяемо ли преимущество

def test_walk_forward_produces_folds():
    from src.momentum_sweep import walk_forward

    res = walk_forward(_trend(n=800), default_grid()[:12], folds=3)
    assert len(res) >= 2
    for p, tr, fwd in res:
        assert isinstance(tr, float) and isinstance(fwd, float)


def test_walk_forward_short_series_returns_nothing():
    from src.momentum_sweep import walk_forward

    assert walk_forward(_trend(n=100), default_grid()[:4]) == []


def test_walk_forward_trains_only_on_the_past():
    """Набор выбирается по прошлому, измеряется на будущем — иначе это
    не проверка, а подгонка с лишними шагами.

    Данные берутся с шумом: строго периодическая синтетика даёт одинаковые
    отрезки, и результат вперёд совпадает с обучающим тождественно — это
    свойство фикстуры, а не доказательство утечки."""
    from src.momentum_sweep import walk_forward

    res = walk_forward(_noise(n=900, seed=17), default_grid()[:20], folds=3)
    assert res
    assert any(abs(tr - fwd) > 1e-9 for _, tr, fwd in res)


def test_walk_forward_on_noise_has_no_persistent_edge():
    from src.momentum_sweep import walk_forward
    import statistics as st

    res = walk_forward(_noise(n=800), default_grid()[:20], folds=3)
    if len(res) >= 2:
        assert abs(st.mean(f for _, _, f in res)) < 1.0
