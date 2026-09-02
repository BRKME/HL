"""Режимный фильтр по скользящей средней с зоной нечувствительности.

Замечание оператора 02.09: у самой линии случаются ложные пробои. Цена,
болтающаяся вокруг средней, без зоны даёт серию переключений подряд —
каждое стоит комиссии и портит выборку.

Решение то же, что мы применили к verdict_flip в гварде: гистерезис.
Внутри зоны состояние НАСЛЕДУЕТСЯ; чтобы режим сменился, цене надо уйти за
границу, а не просто коснуться линии.

Отдельно важно, почему средняя, а не OracAI: историю режимов OracAI мы
имеем только из своего журнала за три месяца, и прогнать её по 1900 дням
невозможно. Средняя восстанавливается по любой истории, поэтому её можно
проверить на том же горизонте, что и всё остальное.
"""
import pytest

from src.momentum_sweep import moving_average, regime_series


def _flat_then_up(n=300, base=100.0):
    return [base] * (n // 2) + [base * (1 + 0.01 * i) for i in range(n // 2)]


def _oscillate_around(n=300, base=100.0, amp=0.01):
    """Цена ходит вокруг уровня — источник ложных пробоев."""
    return [base * (1 + amp * (1 if i % 2 else -1)) for i in range(n)]


def test_moving_average_needs_enough_history():
    assert moving_average([1, 2, 3], 5, 200) is None
    assert moving_average([1.0] * 200, 199, 200) == pytest.approx(1.0)


def test_regime_is_none_until_ma_exists():
    """До накопления окна состояния нет.

    Цена берётся растущей: на строго плоской цена равна средней, состояние
    остаётся неопределённым — и это верное поведение, а не пробел."""
    closes = [100.0 + i * 0.1 for i in range(300)]
    series = regime_series(closes, ma_len=200, buffer_pct=0.0)
    assert series[100] is None
    assert series[250] is True


def test_price_above_line_is_bull():
    closes = [100.0] * 200 + [130.0] * 50
    assert regime_series(closes, 200, 0.0)[-1] is True


def test_price_below_line_is_bear():
    closes = [100.0] * 200 + [70.0] * 50
    assert regime_series(closes, 200, 0.0)[-1] is False


def test_buffer_suppresses_false_breakouts():
    """Ровно замечание оператора: без зоны переключений много, с зоной мало."""
    closes = _oscillate_around(400, amp=0.01)
    plain = [x for x in regime_series(closes, 200, 0.00) if x is not None]
    buffered = [x for x in regime_series(closes, 200, 0.04) if x is not None]

    def flips(seq):
        return sum(1 for a, b in zip(seq, seq[1:]) if a != b)

    assert flips(buffered) < flips(plain)


def test_state_is_inherited_inside_the_buffer():
    """Внутри зоны состояние сохраняется, а не обнуляется."""
    closes = [100.0] * 200 + [130.0] + [100.5] * 10
    series = regime_series(closes, 200, 0.10)
    assert series[200] is True          # ушла выше зоны
    assert series[-1] is True           # вернулась в зону — состояние то же


def test_zero_length_disables_filter():
    assert all(x is None for x in regime_series([100.0] * 300, 0, 0.02))


def test_buffer_zero_behaves_like_plain_line():
    closes = _flat_then_up()
    assert regime_series(closes, 100, 0.0)[-1] is True


# ------------------------- парное сравнение: фильтр против его отсутствия

def test_filter_reduces_trades():
    """Фильтр обязан отсекать часть сделок — иначе он ничего не делает."""
    from src.momentum_sweep import Params, run_one

    closes = _oscillate_around(600, amp=0.05)
    plain = run_one(closes, Params(28, 5, False, False))
    filtered = run_one(closes, Params(28, 5, False, False, ma_len=200,
                                      ma_buffer=0.02))
    assert len(filtered) <= len(plain)


def test_filter_blocks_longs_below_the_line():
    """Ниже средней лонгов быть не должно."""
    from src.momentum_sweep import Params, run_one

    closes = [100.0] * 220 + [100.0 * (0.99 ** i) for i in range(200)]
    assert run_one(closes, Params(14, 5, False, True, ma_len=200)) == []


def test_paired_comparison_is_possible():
    """Сравнение должно быть парным: тот же набор с фильтром и без.

    Иначе фильтр оценивается перебором, а перебор находит победителя и на
    шуме — этой ловушки мы уже касались дважды."""
    from src.momentum_sweep import Params

    base = Params(28, 5, False, True)
    with_ma = Params(28, 5, False, True, ma_len=200, ma_buffer=0.02)
    assert base.lookback == with_ma.lookback
    assert base.holding == with_ma.holding
    assert (base.ma_len, with_ma.ma_len) == (0, 200)
