"""Живёт ли преимущество китового сигнала после задержки.

Источники сходятся: даже с оповещениями в реальном времени следующий за
китом отстаёт — в лучшем случае на секунды, на практике на минуты, а на
неликвидных монетах разрыв съедает бóльшую часть преимущества. Проверить
это утверждение можно только измерением, и данные у нас есть: 120 тысяч
заполнений 234 китов за 108 дней.

Замысел прост. Для каждого крупного открытия позиции берём цену кита и
цену через задержку D, затем считаем доходность входа с этой задержкой на
горизонте H. Если преимущество живёт только в первые минуты — идея
закрыта: мы физически не можем оказаться там вовремя. Если держится
сутки — идея рабочая, и тогда стоит строить отбор кошельков.

Чего здесь нет: комиссий и проскальзывания. Как и в прочих наших
бэктестах, это верхняя оценка — если преимущества нет здесь, в жизни его
нет тем более.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional, Sequence

MIN_NOTIONAL_USD = 100_000        # мельче — шум, не «кит»
OPEN_DIRECTIONS = ("Open Long", "Open Short")
DELAYS_MIN = (0, 5, 15, 60, 240, 1440)     # от мгновенного до суток
HORIZONS_H = (4, 24, 72)


@dataclass(frozen=True)
class DelayResult:
    delay_min: int
    horizon_h: int
    n: int
    avg_pct: Optional[float]
    median_pct: Optional[float]
    win_rate: Optional[float]


def _price_at(prices: Sequence[tuple[int, float]], t_ms: int,
              tolerance_ms: int) -> Optional[float]:
    """Первая цена не раньше t_ms и не позже допуска.

    Бинарный поиск, а не проход: список отсортирован, а первая версия шла
    линейно и обрывалась `break` на первой же записи — выборки выходили по
    одному-три наблюдения там, где их сотни.
    """
    import bisect

    i = bisect.bisect_left(prices, (t_ms, float("-inf")))
    if i >= len(prices):
        return None
    ts, px = prices[i]
    return px if ts - t_ms <= tolerance_ms else None


def build_price_index(fills: Sequence[dict]) -> dict[str, list]:
    """Цены по монете из заполнений китов.

    ВНИМАНИЕ (найдено 02.09): этого источника НЕДОСТАТОЧНО для горизонтов
    в часы и сутки. Заполнения китов — не котировки: они возникают лишь
    тогда, когда кит торгует. По BTC ближайшая цена через 4 часа после
    сделки находится в среднем через 129 минут, через сутки — через 578.
    Из 7777 промежутков 79 длиннее четырёх часов.

    Поэтому замер на этом индексе давал выборки по одному-три наблюдения:
    подавляющее большинство сделок отбраковывалось по допуску. Для
    горизонтов дольше получаса нужны СВЕЧИ — см. build_price_index_from_candles.
    """
    idx: dict[str, list] = {}
    for f in fills:
        coin, px, ts = f.get("coin"), f.get("price"), f.get("time_ms")
        if not coin or px is None or ts is None:
            continue
        idx.setdefault(coin, []).append((int(ts), float(px)))
    for coin in idx:
        idx[coin].sort()
    return idx


def build_price_index_from_candles(candles_by_coin: dict) -> dict[str, list]:
    """Цены из часовых свечей — равномерная сетка без разрывов."""
    idx: dict[str, list] = {}
    for coin, candles in (candles_by_coin or {}).items():
        rows = []
        for c in candles or []:
            if not isinstance(c, dict):
                continue          # мусорная запись не должна ронять разбор
            ts = c.get("t") if c.get("t") is not None else c.get("T")
            px = c.get("c")
            # ts == 0 — законная метка времени, `or` её отбрасывал
            if ts is None or px is None:
                continue
            try:
                rows.append((int(ts), float(px)))
            except (TypeError, ValueError):
                continue
        if rows:
            idx[coin] = sorted(rows)
    return idx


def measure(fills: Sequence[dict], delay_min: int, horizon_h: int,
            min_notional: float = MIN_NOTIONAL_USD,
            price_index: Optional[dict] = None) -> DelayResult:
    idx = price_index if price_index is not None else build_price_index(fills)
    delay_ms = delay_min * 60_000
    horizon_ms = horizon_h * 3_600_000
    tol = max(delay_ms // 2, 30 * 60_000)

    rets = []
    for f in fills:
        if f.get("direction") not in OPEN_DIRECTIONS:
            continue
        if abs(float(f.get("notional_usd") or 0)) < min_notional:
            continue
        coin, ts = f.get("coin"), f.get("time_ms")
        prices = idx.get(coin)
        # `not ts` отбрасывало метку времени 0 — тот же дефект «ноль это
        # ложь», что уже дважды всплыл выше в этом файле.
        if not prices or ts is None:
            continue
        entry = _price_at(prices, int(ts) + delay_ms, tol)
        exit_px = _price_at(prices, int(ts) + delay_ms + horizon_ms, tol)
        if not entry or not exit_px or entry <= 0:
            continue
        side = 1 if f["direction"] == "Open Long" else -1
        rets.append((exit_px / entry - 1.0) * 100 * side)

    if not rets:
        return DelayResult(delay_min, horizon_h, 0, None, None, None)
    return DelayResult(
        delay_min, horizon_h, len(rets),
        statistics.mean(rets), statistics.median(rets),
        sum(1 for r in rets if r > 0) / len(rets))


def decay_table(fills: Sequence[dict],
                delays: Sequence[int] = DELAYS_MIN,
                horizons: Sequence[int] = HORIZONS_H,
                price_index: Optional[dict] = None) -> list[DelayResult]:
    return [measure(fills, d, h, price_index=price_index)
            for h in horizons for d in delays]
