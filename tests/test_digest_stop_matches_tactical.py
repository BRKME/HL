"""Стоп в дайджесте обязан совпадать с тактическим (31.08.2026).

Письма одного дня по одной монете:

    тактический сигнал  HYPE · SL 72.68 (11.3%)
    дайджест            HYPE · стоп 77.04 (5.9%)   — вдвое уже

Причина — мой код от 30.08: свечи для ATR собирались из одних закрытий,
`{"o": c, "h": c, "l": c, "c": c}`. При high = low = close истинный
диапазон схлопывается до |close − close_prev|, ATR занижается втрое, и
стоп получается вдвое уже настоящего.

Слишком узкий стоп опаснее отсутствующего: он выбивает позицию рыночным
шумом, и оператор получает подряд серию мелких убытков там, где сделка
была нормальной. Настоящие свечи в этот момент уже загружены — их просто
отбрасывали.

Стоп теперь считается на тех же данных, что и в тактическом слое, поэтому
два письма про одну монету говорят одно и то же.
"""
import pytest

from src import ta


def _real_candles(n=60, base=100.0):
    out, p = [], base
    for i in range(n):
        out.append({"o": p, "h": p * 1.04, "l": p * 0.96, "c": p * 1.01})
        p *= 1.005
    return out


def test_atr_from_closes_only_understates_range():
    """Демонстрация причины: high = low = close убивает истинный диапазон."""
    real = _real_candles()
    fake = [{"o": c["c"], "h": c["c"], "l": c["c"], "c": c["c"]} for c in real]
    assert ta.atr(real, 14) > ta.atr(fake, 14) * 2


def test_plan_uses_real_candles_when_present():
    from src.whitelist_focus import _entry_plan

    candles = _real_candles()
    closes = [c["c"] for c in candles]
    mark = closes[-1]

    with_real = _entry_plan("X", "LONG", mark,
                            {"candles_closes": closes, "candles": candles})
    with_closes = _entry_plan("X", "LONG", mark, {"candles_closes": closes})
    assert with_real and with_closes
    assert with_real != with_closes, "настоящие свечи обязаны менять стоп"


def test_real_candles_give_wider_stop():
    from src.whitelist_focus import _entry_plan
    import re

    candles = _real_candles()
    closes = [c["c"] for c in candles]
    mark = closes[-1]

    def pct(line):
        m = re.search(r"\(([\d.]+)%\)", line)
        return float(m.group(1)) if m else None

    wide = pct(_entry_plan("X", "LONG", mark,
                           {"candles_closes": closes, "candles": candles}))
    narrow = pct(_entry_plan("X", "LONG", mark, {"candles_closes": closes}))
    assert wide > narrow


def test_missing_candles_falls_back_gracefully():
    from src.whitelist_focus import _entry_plan

    closes = [c["c"] for c in _real_candles()]
    assert _entry_plan("X", "LONG", closes[-1], {"candles_closes": closes})


def test_no_data_prints_nothing():
    from src.whitelist_focus import _entry_plan

    assert _entry_plan("X", "LONG", 100.0, {}) == ""


def test_both_digest_paths_pass_real_candles():
    """Оба места сборки данных дайджеста обязаны отдавать свечи.

    Первая правка задела только одно из двух — второй путь молча остался
    бы со старым узким стопом."""
    import inspect

    from src import daily_monitor

    src = inspect.getsource(daily_monitor)
    assert src.count('"candles":') >= 2, (
        "оба построителя digest coin_data должны передавать полные свечи")
