"""Технический анализ. Чистый Python, без pandas/numpy — экономим dependency.

Все функции принимают `candles` — список dict с ключами o,h,l,c в хронологическом порядке
(старые → новые, как отдаёт HL candleSnapshot).
"""
from __future__ import annotations

from typing import Sequence


def rsi(closes: Sequence[float], period: int = 14) -> float | None:
    """Wilder RSI."""
    if len(closes) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses += -diff
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def ema(values: Sequence[float], period: int) -> float | None:
    """Exponential MA."""
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return round(e, 6)


def atr(candles: Sequence[dict], period: int = 14) -> float | None:
    """Wilder ATR."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["h"]
        low = candles[i]["l"]
        prev_c = candles[i - 1]["c"]
        tr = max(h - low, abs(h - prev_c), abs(low - prev_c))
        trs.append(tr)
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return round(a, 6)


def swing_low(candles: Sequence[dict], lookback: int = 20) -> float | None:
    """Минимум low за последние `lookback` свечей. Простая аппроксимация support."""
    if not candles:
        return None
    window = candles[-lookback:]
    lows = [c["l"] for c in window]
    return round(min(lows), 6) if lows else None


def momentum_pct(closes: Sequence[float], lookback: int) -> float | None:
    """Возврат за `lookback` свечей в процентах от старой цены."""
    if len(closes) <= lookback:
        return None
    old = closes[-lookback - 1]
    new = closes[-1]
    if old == 0:
        return None
    return round((new - old) / old * 100, 2)


def compute_indicators(candles: list[dict], swing_lookback: int = 30) -> dict:
    """Все индикаторы за один проход. Возвращает dict с None если данных мало."""
    closes = [c["c"] for c in candles]
    last = closes[-1] if closes else None

    e50 = ema(closes, 50)
    e200 = ema(closes, 200)

    return {
        "last": last,
        "rsi_d1": rsi(closes, 14),
        "ema50": e50,
        "ema200": e200,
        "above_ema50": (last is not None and e50 is not None and last > e50),
        "above_ema200": (last is not None and e200 is not None and last > e200),
        "vs_ema50_pct": (
            round((last - e50) / e50 * 100, 2)
            if last is not None and e50 not in (None, 0)
            else None
        ),
        "vs_ema200_pct": (
            round((last - e200) / e200 * 100, 2)
            if last is not None and e200 not in (None, 0)
            else None
        ),
        "atr14": atr(candles, 14),
        "swing_low": swing_low(candles, swing_lookback),
        "momentum_7d": momentum_pct(closes, 7),
        "momentum_30d": momentum_pct(closes, 30),
    }
