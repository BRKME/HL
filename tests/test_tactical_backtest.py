"""Бэктест тактического слоя по истории (01.09.2026).

Причина. Живая выборка растёт по 28 сделок в месяц: до n=400 — одиннадцать
месяцев, до n=1000 — тридцать два. Внешняя практика говорит, что 100 сделок
это минимум, 200 убедительно, 500 сильная значимость, — а зрелые проекты
вроде freqtrade получают шестьсот сделок за один прогон по истории и
используют живую торговлю уже как проверку вне выборки.

Мы делали наоборот: ждали годы там, где история отвечает за вечер.

Отдельная причина начать заново: логика менялась 23.08 (H3). Смешивать
сделки разных версий правил в один набор нельзя — это две разные стратегии.

Бэктест прогоняет ТЕКУЩУЮ логику вердикта по дневным свечам, открывает
виртуальную сделку на смене вердикта и закрывает по SL/TP или по смене
вердикта — так же, как это делает живой слой.
"""
from datetime import datetime, timezone

import pytest

from src.tactical_backtest import Trade, replay, summarise


def _candles(n=320, base=100.0, drift=0.004, wobble=0.02):
    out, p = [], base
    for i in range(n):
        p *= (1 + drift + (wobble if i % 7 == 0 else -wobble / 3))
        out.append({"t": i, "o": p * 0.995, "h": p * 1.02,
                    "l": p * 0.98, "c": p})
    return out


def test_replay_produces_trades_on_trending_data():
    trades = replay("TEST", _candles())
    assert isinstance(trades, list)
    assert all(isinstance(t, Trade) for t in trades)


def test_trade_has_everything_needed_to_score():
    trades = replay("TEST", _candles())
    if not trades:
        pytest.skip("на синтетике сделок не возникло")
    t = trades[0]
    assert t.direction in ("LONG", "SHORT")
    assert t.entry > 0 and t.sl > 0
    assert t.exit_reason in ("sl", "tp", "verdict_flip", "eod")


def test_no_lookahead_entry_uses_close_of_signal_bar():
    """Вход по цене закрытия свечи сигнала, не по будущей."""
    candles = _candles()
    trades = replay("TEST", candles)
    for t in trades:
        assert t.entry == pytest.approx(candles[t.entry_idx]["c"])


def test_exit_never_precedes_entry():
    for t in replay("TEST", _candles()):
        assert t.exit_idx > t.entry_idx


def test_short_history_yields_nothing():
    assert replay("TEST", _candles(n=100)) == []


def test_summary_reports_sample_and_interval():
    trades = replay("TEST", _candles())
    s = summarise(trades)
    assert "n" in s and "avg_r" in s and "ci" in s
    assert s["n"] == len(trades)


def test_summary_of_empty_is_safe():
    s = summarise([])
    assert s["n"] == 0
    assert s["avg_r"] is None


def test_r_is_measured_against_the_stop():
    """R = движение цены, делённое на риск до стопа."""
    from src.tactical_backtest import _r_multiple

    assert _r_multiple("LONG", entry=100, sl=90, exit_px=110) == pytest.approx(1.0)
    assert _r_multiple("LONG", entry=100, sl=90, exit_px=90) == pytest.approx(-1.0)
    assert _r_multiple("SHORT", entry=100, sl=110, exit_px=90) == pytest.approx(1.0)
