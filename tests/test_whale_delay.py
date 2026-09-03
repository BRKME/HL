"""Затухание китового сигнала с задержкой (02.09.2026).

Источники утверждают: следующий за китом отстаёт на минуты, и на
неликвидных монетах это съедает бóльшую часть преимущества. Утверждение
проверяемое — у нас 120 тысяч заполнений 234 китов за 108 дней.

Если преимущество живёт только первые минуты, идея закрыта: мы физически
не можем успеть. Если держится сутки — стоит строить отбор кошельков.
"""
import pytest

from src.whale_delay import (
    DelayResult, build_price_index, decay_table, measure,
)

MIN_MS = 60_000
H_MS = 3_600_000


def _fill(coin, price, t_ms, direction="Open Long", notional=500_000):
    return {"coin": coin, "price": price, "time_ms": t_ms,
            "direction": direction, "notional_usd": notional}


def _rising_series(coin="ETH", n=200, start=100.0, step=0.5):
    return [_fill(coin, start + i * step, i * 10 * MIN_MS) for i in range(n)]


def test_price_index_is_sorted_per_coin():
    idx = build_price_index([_fill("ETH", 1, 300), _fill("ETH", 2, 100)])
    assert [t for t, _ in idx["ETH"]] == [100, 300]


def test_rising_market_gives_positive_return_for_longs():
    r = measure(_rising_series(), delay_min=0, horizon_h=4)
    assert r.n > 0
    assert r.avg_pct > 0


def test_short_direction_is_inverted():
    fills = [dict(f, direction="Open Short") for f in _rising_series()]
    r = measure(fills, delay_min=0, horizon_h=4)
    assert r.avg_pct < 0


def test_small_fills_are_ignored():
    fills = [dict(f, notional_usd=1_000) for f in _rising_series()]
    assert measure(fills, 0, 4).n == 0


def test_non_open_fills_are_ignored():
    fills = [dict(f, direction="Close Long") for f in _rising_series()]
    assert measure(fills, 0, 4).n == 0


def test_delay_changes_the_entry_price():
    """Смысл всей проверки: задержка обязана менять результат."""
    fast = measure(_rising_series(), delay_min=0, horizon_h=4)
    slow = measure(_rising_series(), delay_min=240, horizon_h=4)
    assert fast.n and slow.n
    assert fast.avg_pct != slow.avg_pct


def test_decay_table_covers_all_combinations():
    table = decay_table(_rising_series(), delays=(0, 60), horizons=(4, 24))
    assert len(table) == 4
    assert all(isinstance(r, DelayResult) for r in table)


def test_empty_input_is_safe():
    r = measure([], 0, 4)
    assert r.n == 0 and r.avg_pct is None


# --------------------- источник цен: заполнения не годятся для длинных окон

def test_candle_index_is_built_from_hourly_bars():
    from src.whale_delay import build_price_index_from_candles

    candles = {"ETH": [{"t": i * H_MS, "c": 100 + i} for i in range(48)]}
    idx = build_price_index_from_candles(candles)
    assert len(idx["ETH"]) == 48
    assert idx["ETH"] == sorted(idx["ETH"])


def test_candle_index_survives_missing_fields():
    from src.whale_delay import build_price_index_from_candles

    idx = build_price_index_from_candles({"ETH": [{"t": 1}, {"c": 2}, None]})
    assert idx == {}


def test_measure_accepts_external_price_index():
    """Ключевая правка: цены берутся из свечей, а не из заполнений китов.

    Заполнения возникают, только когда кит торгует: по BTC ближайшая цена
    через 4 часа после сделки находится в среднем через 129 минут, через
    сутки — через 578. Замер на них давал выборки по одному-три
    наблюдения вместо сотен."""
    from src.whale_delay import build_price_index_from_candles

    fills = [_fill("ETH", 100, 0)]
    candles = {"ETH": [{"t": i * H_MS, "c": 100 + i} for i in range(48)]}
    r = measure(fills, delay_min=0, horizon_h=4,
                price_index=build_price_index_from_candles(candles))
    assert r.n == 1
    assert r.avg_pct == pytest.approx(4.0)


def test_zero_timestamp_is_valid():
    """«Ноль это ложь» трижды подряд отбрасывал корректные данные:
    метку времени 0 в свече, в заполнении и в индексе."""
    from src.whale_delay import build_price_index

    idx = build_price_index([_fill("ETH", 100, 0)])
    assert idx["ETH"] == [(0, 100.0)]
