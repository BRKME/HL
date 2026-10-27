"""Шорт новых листингов Hyperliquid со второго дня (27.10.2026).

Основание — событийные исследования спотовых листингов 2019–2021: рост до и
в день листинга, со второго дня отрицательный дрейф. Для перпов HL никто
этого не проверял. Предрегистрация и критерии — docs/OPERATING_POLICY.md,
запись 27.10.

Риск, ради которого стоп считается по максимумам и гэпам: сквиз. На HL
был случай JELLYJELLY — цену мема разогнали, чтобы ликвидировать шорты.
"""
from __future__ import annotations

import statistics
from typing import Optional, Sequence

DAY_MS = 86_400_000
LISTING_GRACE_D = 3     # первая свеча ближе к началу окна — монета жила раньше
ENTRY_IDX = 1           # вход по закрытию первого полного дня («со второго»)


def listing_index(candles: Sequence[dict], window_start_ms: int) -> Optional[int]:
    """Индекс свечи листинга или None, если монета торговалась до окна."""
    if not candles:
        return None
    if candles[0]["t"] - window_start_ms < LISTING_GRACE_D * DAY_MS:
        return None
    return 0


def short_return(candles: Sequence[dict], entry_idx: int, horizon: int,
                 stop_pct: Optional[float]) -> Optional[tuple[float, bool]]:
    """Доходность шорта в % и признак выбивания стопом.

    Стоп — по максимуму дня; если день ОТКРЫЛСЯ выше стопа, выход по
    открытию: при гэпе убыток больше заявленного, и прятать это нельзя.
    """
    if entry_idx + horizon >= len(candles):
        return None
    entry = float(candles[entry_idx]["c"])
    if entry <= 0:
        return None
    if stop_pct is not None:
        level = entry * (1 + stop_pct / 100)
        for k in range(entry_idx + 1, entry_idx + horizon + 1):
            o = float(candles[k].get("o") or candles[k]["c"])
            h = float(candles[k].get("h") or candles[k]["c"])
            if o >= level:
                return -(o / entry - 1) * 100, True
            if h >= level:
                return -stop_pct, True
    exit_px = float(candles[entry_idx + horizon]["c"])
    return -(exit_px / entry - 1) * 100, False


def split_halves_edge(events: Sequence[tuple], control_avg: float
                      ) -> tuple[Optional[float], Optional[float]]:
    """Обгон контроля в ранней и поздней половине событий по времени.

    Защита от подгонки на сетке: лучший из 24 вариантов на одной выборке
    найдётся всегда; настоящий эффект держится в обеих половинах.
    """
    ev = sorted(events, key=lambda e: e[0])
    if len(ev) < 2:
        return None, None
    mid = len(ev) // 2
    lo = statistics.mean(r for _, r in ev[:mid]) - control_avg
    hi = statistics.mean(r for _, r in ev[mid:]) - control_avg
    return lo, hi
