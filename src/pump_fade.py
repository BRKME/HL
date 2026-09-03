"""Шорт экстремально выросших монет: возврат к среднему.

Идея оператора и первая за всё исследование, которая идёт ПРОТИВ тренда, а
не за ним. У неё два независимых основания, работающих в одну сторону:

* возврат к среднему после экстремального движения;
* фандинг. Во время роста ставка уходит в плюс, и ШОРТ ЕЁ ПОЛУЧАЕТ. Весь
  месяц наши сигналы были лонгами, платившими 11% годовых, — здесь знак
  переворачивается в нашу пользу.

И один серьёзный довод против, который надо мерить, а не игнорировать:
асимметрия. Убыток шорта не ограничен (монета может вырасти ещё втрое),
прибыль ограничена сотней процентов. Плюс сквизы — выросшие монеты
выносят шорты резко.

Отдельная оговорка: для BTC литература утверждает обратное — доходность
ВЫШЕ после локальных максимумов. Но то мейджор, а речь о средней
капитализации, где механика иная. Проверяем, а не предполагаем.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional, Sequence

DEFAULT_PUMP_PCT = 50.0        # рост за окно, считающийся экстремальным
DEFAULT_WINDOW_D = 7
DEFAULT_HORIZONS_D = (3, 7, 14, 30)
FUNDING_APR_PCT = 11.0         # шорт ПОЛУЧАЕТ при положительной ставке


@dataclass(frozen=True)
class PumpEvent:
    coin: str
    idx: int
    run_up_pct: float
    entry: float


@dataclass(frozen=True)
class FadeResult:
    horizon_d: int
    n: int
    avg_pct: Optional[float]          # доходность ШОРТА, без фандинга
    median_pct: Optional[float]
    win_rate: Optional[float]
    worst_pct: Optional[float]        # худшая сделка — мера риска сквиза
    avg_with_funding_pct: Optional[float]
    stopped_share: Optional[float] = None


def find_pumps(closes: Sequence[float], pump_pct: float = DEFAULT_PUMP_PCT,
               window_d: int = DEFAULT_WINDOW_D,
               cooldown_d: int = 14) -> list[PumpEvent]:
    """Точки, где цена выросла на pump_pct за window_d дней.

    cooldown_d не даёт одному затяжному росту породить два десятка
    «событий» подряд: это та же передискретизация, что мы уже ловили
    трижды — у китов, в журнале и в барьерах.
    """
    out: list[PumpEvent] = []
    last = -10**9
    for i in range(window_d, len(closes)):
        base = closes[i - window_d]
        if base <= 0:
            continue
        run = (closes[i] / base - 1.0) * 100
        if run >= pump_pct and i - last >= cooldown_d:
            out.append(PumpEvent("", i, run, closes[i]))
            last = i
    return out


def evaluate(closes: Sequence[float], events: Sequence[PumpEvent],
             horizon_d: int, funding_apr: float = FUNDING_APR_PCT,
             stop_pct: Optional[float] = None,
             highs: Optional[Sequence[float]] = None) -> FadeResult:
    """Что дал бы шорт на горизонте horizon_d.

    Доходность считается для шорта: цена упала — прибыль. Фандинг
    добавляется, а не вычитается: при положительной ставке шорт получает.
    """
    rets = []
    stopped = 0
    for e in events:
        j = e.idx + horizon_d
        if j >= len(closes) or e.entry <= 0:
            continue

        # Стоп проверяется ПО МАКСИМУМАМ, а не по закрытиям: цена может
        # выбить стоп внутри дня и вернуться, и считать это выживанием
        # значило бы завышать результат. При плече 5x ликвидация наступает
        # около -20% — стоп шире неё бессмыслен, биржа закроет раньше.
        if stop_pct is not None:
            hit = False
            for k in range(e.idx + 1, j + 1):
                level = (highs[k] if highs and k < len(highs) else closes[k])
                if (level / e.entry - 1.0) * 100 >= stop_pct:
                    hit = True
                    break
            if hit:
                rets.append(-stop_pct)
                stopped += 1
                continue

        rets.append((e.entry / closes[j] - 1.0) * 100)
    if not rets:
        return FadeResult(horizon_d, 0, None, None, None, None, None)

    funding_gain = funding_apr / 365 * horizon_d
    return FadeResult(
        horizon_d, len(rets),
        statistics.mean(rets), statistics.median(rets),
        sum(1 for r in rets if r > 0) / len(rets),
        min(rets),
        statistics.mean(rets) + funding_gain,
        stopped / len(rets) if rets else None)


def baseline(closes: Sequence[float], horizon_d: int, step: int = 7,
             funding_apr: float = FUNDING_APR_PCT) -> FadeResult:
    """Шорт в СЛУЧАЙНЫЙ момент — контроль.

    Без него нельзя отличить «работает шорт после роста» от «работает шорт
    вообще, потому что монета падала». Эту ошибку мы уже делали.
    """
    rets = []
    for i in range(0, len(closes) - horizon_d, step):
        if closes[i] <= 0:
            continue
        rets.append((closes[i] / closes[i + horizon_d] - 1.0) * 100)
    if not rets:
        return FadeResult(horizon_d, 0, None, None, None, None, None)
    funding_gain = funding_apr / 365 * horizon_d
    return FadeResult(
        horizon_d, len(rets), statistics.mean(rets),
        statistics.median(rets),
        sum(1 for r in rets if r > 0) / len(rets), min(rets),
        statistics.mean(rets) + funding_gain)
