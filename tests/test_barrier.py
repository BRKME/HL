"""Соотношения риск/прибыль против теоретической базы (02.09.2026).

Замысел оператора: подойти как к ставкам и задать соотношение
математически — стоп −1%, цель +5%.

Точный ответ даёт задача о двух барьерах: для случайного блуждания без
сноса вероятность коснуться +A раньше, чем −B, равна B/(A+B), и
матожидание тождественно ноль ПРИ ЛЮБОМ соотношении. Узкий стоп покупается
падением вероятности выигрыша ровно в той же пропорции. Соотношение само
по себе преимущества не создаёт.

Но крипта случайным блужданием быть не обязана. Если в цене есть моментум,
наблюдаемая вероятность окажется ВЫШЕ теоретической — и вот это уже
преимущество. Здесь проверяется именно разница «наблюдаемое минус
теоретическое», а не сам факт прибыльности.
"""
import pytest

from src.barrier_test import grid, measure, theoretical_p


def _random_walk(n=3000, base=100.0, step=0.004, seed=11):
    """Чистое блуждание: тут наблюдаемое обязано совпасть с теорией."""
    import random
    rng = random.Random(seed)
    out, p = [], base
    for _ in range(n):
        p *= (1 + rng.choice((-step, step)))
        out.append({"c": p, "h": p * 1.001, "l": p * 0.999})
    return out


def _trending(n=3000, base=100.0, drift=0.001, noise=0.004, seed=7):
    import random
    rng = random.Random(seed)
    out, p = [], base
    for _ in range(n):
        p *= (1 + drift + rng.uniform(-noise, noise))
        out.append({"c": p, "h": p * 1.002, "l": p * 0.998})
    return out


# ------------------------------------------------------------ теория

@pytest.mark.parametrize("sl,tp,expected", [
    (1, 5, 1 / 6), (1, 3, 0.25), (5, 5, 0.5), (2, 4, 1 / 3),
])
def test_theoretical_probability(sl, tp, expected):
    assert theoretical_p(sl, tp) == pytest.approx(expected)


def test_theoretical_expectancy_is_zero():
    """Ключевое: при любом соотношении матожидание ноль."""
    for sl, tp in ((1, 5), (1, 10), (3, 3), (2, 7)):
        p = theoretical_p(sl, tp)
        assert p * tp - (1 - p) * sl == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------- эмпирика

def test_random_walk_matches_theory():
    """На блуждании наблюдаемое не должно заметно отличаться от теории —
    иначе врёт сам замер, а не рынок."""
    r = measure(_random_walk(), sl_pct=1, tp_pct=3, max_bars=400)
    assert r.n > 50
    assert abs(r.edge_pp) < 12


def test_trending_market_beats_theory():
    """При сносе цель достигается чаще теоретического — это и есть
    признак, ради которого замер делается."""
    r = measure(_trending(), sl_pct=1, tp_pct=3, max_bars=400)
    assert r.edge_pp > 0


def test_both_touched_in_one_bar_counts_as_stop():
    """Внутри свечи порядок неизвестен; выгодный исход предполагать нельзя."""
    candles = [{"c": 100, "h": 100, "l": 100}] * 10
    candles += [{"c": 100, "h": 110, "l": 90}] * 300
    r = measure(candles, sl_pct=5, tp_pct=5, max_bars=200)
    assert r.hit_sl > 0
    assert r.hit_tp == 0


def test_short_side_is_mirrored():
    down = [{"c": 100 * (0.999 ** i), "h": 100 * (0.999 ** i) * 1.001,
             "l": 100 * (0.999 ** i) * 0.999} for i in range(3000)]
    long_r = measure(down, 1, 3, side=1, max_bars=400)
    short_r = measure(down, 1, 3, side=-1, max_bars=400)
    assert short_r.observed_p > long_r.observed_p


def test_step_thins_overlapping_entries():
    """Соседние свечи дают почти одинаковый исход — считать их
    независимыми наблюдениями нельзя (та же передискретизация, что
    ловили в журнале и у китов)."""
    dense = measure(_random_walk(), 1, 3, max_bars=200, step=1)
    sparse = measure(_random_walk(), 1, 3, max_bars=200, step=20)
    assert sparse.n < dense.n


def test_short_history_is_safe():
    r = measure([{"c": 1, "h": 1, "l": 1}] * 5, 1, 3)
    assert r.n == 0 and r.observed_p is None


def test_grid_covers_pairs():
    out = grid(_random_walk(), pairs=((1, 3), (2, 4)), max_bars=300)
    assert len(out) == 2
    assert {(r.sl_pct, r.tp_pct) for r in out} == {(1, 3), (2, 4)}
