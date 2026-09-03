"""Стечение признаков на BTC и ETH (02.09.2026).

Каждый компонент измерялся по отдельности и ни один не дал преимущества.
Источники сходятся: так и должно быть — преимущество не в одиночном
сигнале, а в стечении. Одна крупная покупка может быть управлением
казначейством, маршрутизацией или шумом; подтверждает её совпадение с
другими признаками.

Объём не измерялся ни разу — это пробел в исследовании, а не проверенный
факт. Порог по размеру сделки кита снижен со $100k до $25k: событий
становится 882 вместо 235 за 108 дней. Сам порог произволен, потому он
параметр.

Критерий, записанный ДО прогона: группа «все три признака» должна
обгонять «все события кита» минимум на 1 п.п. средней доходности при
n≥30. Меньше тридцати — «судить нельзя», та же мерка, что применялась ко
всем прежним гипотезам.
"""
import pytest

from src.confluence import (
    DEFAULT_MIN_NOTIONAL, Event, annotate, evaluate, forward_return,
    whale_events,
)

H = 3_600_000


def _fill(coin, direction, notional, t_ms):
    return {"coin": coin, "direction": direction,
            "notional_usd": notional, "time_ms": t_ms}


def _candles(n=400, start=100.0, step=0.5, vol=1000.0):
    return [{"t": i * H, "c": start + i * step, "v": vol} for i in range(n)]


# ----------------------------------------------------------- отбор событий

def test_only_opens_are_events():
    fills = [_fill("BTC", "Open Long", 50_000, 0),
             _fill("BTC", "Close Long", 50_000, H)]
    assert len(whale_events(fills)) == 1


def test_threshold_filters_small_fills():
    fills = [_fill("BTC", "Open Long", 1_000, 0)]
    assert whale_events(fills) == []
    assert len(whale_events(fills, min_notional=500)) == 1


def test_default_threshold_is_lowered_to_25k():
    assert DEFAULT_MIN_NOTIONAL == 25_000


def test_other_coins_excluded():
    assert whale_events([_fill("SOL", "Open Long", 100_000, 0)]) == []


def test_short_side_is_negative():
    e = whale_events([_fill("ETH", "Open Short", 50_000, 0)])[0]
    assert e.side == -1


def test_zero_timestamp_is_kept():
    """«Ноль это ложь» уже трижды отбрасывал верные данные."""
    assert len(whale_events([_fill("BTC", "Open Long", 50_000, 0)])) == 1


# ------------------------------------------------------------- разметка

def test_trend_agreement_on_a_rising_market():
    events = [Event("BTC", 300 * H, 1, 50_000)]
    marked = annotate(events, {"BTC": _candles()})
    assert marked[0].trend_agrees is True


def test_trend_disagrees_for_short_in_uptrend():
    marked = annotate([Event("BTC", 300 * H, -1, 50_000)],
                      {"BTC": _candles()})
    assert marked[0].trend_agrees is False


def test_volume_spike_detected():
    candles = _candles()
    candles[299]["v"] = 100_000.0
    marked = annotate([Event("BTC", 300 * H, 1, 50_000)], {"BTC": candles})
    assert marked[0].volume_high is True


def test_flat_volume_is_not_high():
    marked = annotate([Event("BTC", 300 * H, 1, 50_000)],
                      {"BTC": _candles()})
    assert marked[0].volume_high is False


def test_early_events_are_left_unmarked():
    """Без истории признаки посчитать нельзя — и выдумывать их нельзя."""
    marked = annotate([Event("BTC", 5 * H, 1, 50_000)], {"BTC": _candles()})
    assert marked[0].trend_agrees is None


# ------------------------------------------------------------ доходность

def test_forward_return_follows_side():
    rows = [(i * H, 100.0 + i, 0.0) for i in range(50)]
    assert forward_return(rows, 0, 10, 1) == pytest.approx(10.0)
    assert forward_return(rows, 0, 10, -1) == pytest.approx(-10.0)


def test_forward_return_none_past_data_end():
    rows = [(i * H, 100.0, 0.0) for i in range(5)]
    assert forward_return(rows, 0, 24, 1) is None


def test_evaluate_reports_all_four_groups():
    events = annotate([Event("BTC", 300 * H, 1, 50_000)],
                      {"BTC": _candles()})
    out = evaluate(events, {"BTC": _candles()}, horizon_h=24)
    assert [o.label for o in out] == [
        "все события кита", "+ тренд согласен", "+ объём выше нормы",
        "все три признака"]


def test_evaluate_handles_no_events():
    out = evaluate([], {"BTC": _candles()})
    assert all(o.n == 0 for o in out)
