"""Стечение признаков на BTC и ETH: кит + тренд + объём.

Каждый компонент мы измеряли по отдельности, и ни один не дал
преимущества. Источники сходятся на том, что так и должно быть:
преимущество не в одиночном сигнале, а в стечении — одна крупная покупка
может быть управлением казначейством, маршрутизацией через биржу или
шумом, и подтверждает её только совпадение с другими признаками.

Здесь проверяется именно это. Объём мы не измеряли ни разу — пробел в
исследовании, а не проверенный факт.

Порог по размеру сделки кита снижен со $100k до $25k: на $100k событий
было 235 за 108 дней, на $25k — 882, и это уже рабочая выборка. Сам порог
произволен, поэтому он параметр, а не константа.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional, Sequence

DEFAULT_MIN_NOTIONAL = 25_000
COINS = ("BTC", "ETH")


@dataclass(frozen=True)
class Event:
    coin: str
    ts_ms: int
    side: int                 # +1 лонг, -1 шорт
    notional: float
    trend_agrees: Optional[bool] = None
    volume_high: Optional[bool] = None


@dataclass(frozen=True)
class Outcome:
    label: str
    n: int
    avg_pct: Optional[float]
    median_pct: Optional[float]
    win_rate: Optional[float]


def whale_events(fills: Sequence[dict], coins: Sequence[str] = COINS,
                 min_notional: float = DEFAULT_MIN_NOTIONAL) -> list[Event]:
    out = []
    for f in fills:
        if f.get("coin") not in coins:
            continue
        d = f.get("direction")
        if d not in ("Open Long", "Open Short"):
            continue
        notional = abs(float(f.get("notional_usd") or 0))
        if notional < min_notional:
            continue
        ts = f.get("time_ms")
        if ts is None:
            continue
        out.append(Event(coin=f["coin"], ts_ms=int(ts),
                         side=1 if d == "Open Long" else -1,
                         notional=notional))
    return sorted(out, key=lambda e: e.ts_ms)


def _ema(values: Sequence[float], span: int) -> Optional[float]:
    if len(values) < span:
        return None
    k = 2 / (span + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def annotate(events: Sequence[Event], candles_by_coin: dict,
             vol_window: int = 24, vol_multiple: float = 1.5
             ) -> list[Event]:
    """Разметить события трендом и объёмом НА МОМЕНТ СОБЫТИЯ.

    Только прошлые свечи: заглядывание вперёд сделало бы проверку
    бессмысленной. Объём считается «высоким», если он превышает медиану
    за окно в заданное число раз.
    """
    import bisect

    prepared = {}
    for coin, candles in (candles_by_coin or {}).items():
        rows = []
        for c in candles or []:
            if not isinstance(c, dict):
                continue
            ts = c.get("t") if c.get("t") is not None else c.get("T")
            if ts is None or c.get("c") is None:
                continue
            rows.append((int(ts), float(c["c"]),
                         float(c.get("v") or c.get("V") or 0)))
        prepared[coin] = sorted(rows)

    out = []
    for e in events:
        rows = prepared.get(e.coin) or []
        i = bisect.bisect_left(rows, (e.ts_ms, float("-inf"), 0.0))
        if i < 30:
            out.append(e)
            continue
        closes = [r[1] for r in rows[:i]]
        vols = [r[2] for r in rows[max(0, i - vol_window):i]]

        fast, slow = _ema(closes[-50:], 12), _ema(closes[-200:], 26)
        trend = None
        if fast is not None and slow is not None:
            trend = (fast > slow) if e.side > 0 else (fast < slow)

        vol_high = None
        if len(vols) >= 5 and any(vols):
            med = statistics.median(vols)
            vol_high = med > 0 and vols[-1] >= med * vol_multiple

        out.append(Event(e.coin, e.ts_ms, e.side, e.notional, trend, vol_high))
    return out


def forward_return(rows: Sequence[tuple], ts_ms: int, horizon_h: int,
                   side: int, tolerance_ms: int = 3_600_000
                   ) -> Optional[float]:
    import bisect

    i = bisect.bisect_left(rows, (ts_ms, float("-inf"), 0.0))
    j = bisect.bisect_left(rows, (ts_ms + horizon_h * 3_600_000,
                                  float("-inf"), 0.0))
    if i >= len(rows) or j >= len(rows):
        return None
    if rows[i][0] - ts_ms > tolerance_ms:
        return None
    entry, exit_px = rows[i][1], rows[j][1]
    if entry <= 0:
        return None
    return (exit_px / entry - 1.0) * 100 * side


def evaluate(events: Sequence[Event], candles_by_coin: dict,
             horizon_h: int = 24) -> list[Outcome]:
    """Сравнить группы: все события, кит+тренд, кит+объём, все три."""
    prepared = {}
    for coin, candles in (candles_by_coin or {}).items():
        rows = []
        for c in candles or []:
            if not isinstance(c, dict):
                continue
            ts = c.get("t") if c.get("t") is not None else c.get("T")
            if ts is None or c.get("c") is None:
                continue
            rows.append((int(ts), float(c["c"]), 0.0))
        prepared[coin] = sorted(rows)

    groups = {
        "все события кита": lambda e: True,
        "+ тренд согласен": lambda e: e.trend_agrees is True,
        "+ объём выше нормы": lambda e: e.volume_high is True,
        "все три признака": lambda e: (e.trend_agrees is True
                                       and e.volume_high is True),
    }
    out = []
    for label, pred in groups.items():
        rets = []
        for e in events:
            if not pred(e):
                continue
            r = forward_return(prepared.get(e.coin) or [], e.ts_ms,
                               horizon_h, e.side)
            if r is not None:
                rets.append(r)
        out.append(Outcome(
            label, len(rets),
            statistics.mean(rets) if rets else None,
            statistics.median(rets) if rets else None,
            (sum(1 for r in rets if r > 0) / len(rets)) if rets else None))
    return out
