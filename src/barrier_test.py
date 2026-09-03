"""Проверка соотношений риск/прибыль против теоретической базы.

Замысел оператора: подойти как к ставкам и задать соотношение
математически — например стоп −1%, цель +5%.

Точный ответ на это даёт задача о двух барьерах. Для случайного блуждания
без сноса вероятность коснуться +A раньше, чем −B, равна B/(A+B). Отсюда
матожидание тождественно ноль ПРИ ЛЮБОМ соотношении: узкий стоп покупается
снижением вероятности выигрыша ровно в той же пропорции. Соотношение
риск/прибыль само по себе преимущества не создаёт — оно меняет форму
распределения, не сдвигая среднее. С комиссиями итог отрицателен.

Но это верно для СЛУЧАЙНОГО блуждания, а крипта им быть не обязана. Если
в цене есть моментум, наблюдаемая вероятность достижения цели окажется
ВЫШЕ теоретической, и тогда соотношение выбирать имеет смысл. Здесь это и
проверяется — на исторических свечах, а не в предположениях.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class BarrierResult:
    sl_pct: float
    tp_pct: float
    n: int
    hit_tp: int
    hit_sl: int
    timeout: int
    observed_p: Optional[float]
    theoretical_p: float
    edge_pp: Optional[float]        # наблюдаемая минус теоретическая, п.п.
    expectancy_pct: Optional[float]


def theoretical_p(sl_pct: float, tp_pct: float) -> float:
    """B/(A+B) — вероятность цели раньше стопа при случайном блуждании."""
    total = sl_pct + tp_pct
    return sl_pct / total if total > 0 else 0.0


def _first_touch(highs: Sequence[float], lows: Sequence[float],
                 entry: float, sl: float, tp: float, side: int,
                 max_bars: int) -> str:
    """Что коснулось раньше. При касании обоих в одной свече — стоп.

    Консервативно: внутри свечи порядок неизвестен, и предполагать
    выгодный исход значило бы завышать результат.
    """
    for k in range(min(max_bars, len(highs))):
        hi, lo = highs[k], lows[k]
        if side > 0:
            if lo <= sl:
                return "sl"
            if hi >= tp:
                return "tp"
        else:
            if hi >= sl:
                return "sl"
            if lo <= tp:
                return "tp"
    return "timeout"


def measure(candles: Sequence[dict], sl_pct: float, tp_pct: float,
            side: int = 1, max_bars: int = 240,
            step: int = 6) -> BarrierResult:
    """Пройти по истории и посчитать, что срабатывало раньше.

    step — прореживание точек входа: соседние свечи дают почти одинаковые
    исходы, и считать их независимыми наблюдениями нельзя. Это та же
    передискретизация, что мы уже ловили в журнале и у китов.
    """
    rows = []
    for c in candles or []:
        if not isinstance(c, dict):
            continue
        try:
            rows.append((float(c["c"]), float(c["h"]), float(c["l"])))
        except (KeyError, TypeError, ValueError):
            continue
    if len(rows) < max_bars + 10:
        return BarrierResult(sl_pct, tp_pct, 0, 0, 0, 0, None,
                             theoretical_p(sl_pct, tp_pct), None, None)

    hit_tp = hit_sl = timeout = 0
    for i in range(0, len(rows) - max_bars - 1, step):
        entry = rows[i][0]
        if entry <= 0:
            continue
        sl = entry * (1 - sl_pct / 100 * side)
        tp = entry * (1 + tp_pct / 100 * side)
        highs = [r[1] for r in rows[i + 1:i + 1 + max_bars]]
        lows = [r[2] for r in rows[i + 1:i + 1 + max_bars]]
        res = _first_touch(highs, lows, entry, sl, tp, side, max_bars)
        if res == "tp":
            hit_tp += 1
        elif res == "sl":
            hit_sl += 1
        else:
            timeout += 1

    decided = hit_tp + hit_sl
    n = decided + timeout
    if not decided:
        return BarrierResult(sl_pct, tp_pct, n, hit_tp, hit_sl, timeout,
                             None, theoretical_p(sl_pct, tp_pct), None, None)

    observed = hit_tp / decided
    theo = theoretical_p(sl_pct, tp_pct)
    expectancy = observed * tp_pct - (1 - observed) * sl_pct
    return BarrierResult(sl_pct, tp_pct, n, hit_tp, hit_sl, timeout,
                         observed, theo, (observed - theo) * 100, expectancy)


def grid(candles: Sequence[dict],
         pairs: Sequence[tuple[float, float]] = (
             (1, 5), (1, 3), (1, 2), (2, 4), (2, 6), (3, 3), (5, 5),
         ), side: int = 1, max_bars: int = 240) -> list[BarrierResult]:
    return [measure(candles, sl, tp, side=side, max_bars=max_bars)
            for sl, tp in pairs]
