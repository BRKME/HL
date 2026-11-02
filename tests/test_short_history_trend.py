"""Короткая история — не «тренд вниз» (02.11.2026).

Повод — добавление LIT (Lighter): у молодой монеты меньше 200 дневных
свечей, EMA200 не считается. `compute_indicators` отдавал при этом
above_ema200 = False — «цена НИЖЕ средней», и при цене ниже EMA50 модель
выводила «тренд вниз» → SHORT по монете, о долгом тренде которой не знает
ничего. Пятый случай класса «пусто — это ложь».
"""
from src.eth_focus import compute_verdict_pair
from src.ta import compute_indicators


def _falling(n):
    return [{"o": 100 - i * 0.3, "h": 100 - i * 0.3, "l": 100 - i * 0.3,
             "c": 100 - i * 0.3} for i in range(n)]


def test_missing_ema200_is_unknown_not_below():
    ind = compute_indicators(_falling(120))
    assert ind["ema200"] is None
    assert ind["above_ema200"] is None
    assert ind["above_ema50"] is False        # EMA50 посчитана — факт


def test_short_history_does_not_yield_downtrend_short():
    ta = compute_indicators(_falling(120))
    (raw, raw_why), _ = compute_verdict_pair(ta, None, None, 0, "BEAR",
                                             "EARLY_BULL")
    assert raw != "SHORT"
    assert "тренд вниз" not in raw_why


def test_full_history_still_reads_downtrend():
    ind = compute_indicators(_falling(260))
    assert ind["above_ema200"] is False and ind["above_ema50"] is False
