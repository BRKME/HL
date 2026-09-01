"""Перебор моделей моментума с разделением на обучение и проверку.

Собран 01.09.2026 по итогам внешнего обзора. Что литература говорит про
крипто и чего наша модель не делает:

* Временнáя моментум-стратегия обгоняет кросс-секционную при высокой
  корреляции активов (31.96% годовых против худшего результата у CSM).
  У нас девять коррелированных альтов и отбор по относительной силе —
  то есть слабейший вариант.
* Лучший параметр в исследовании крипто-моментума: окно 28 дней,
  удержание 5 дней, Sharpe 1.51 против 0.84 у рынка. У нас EMA50/200 —
  окно втрое длиннее.
* Доходность BTC ВЫШЕ после локальных максимумов; покупка у минимумов
  менее прибыльна. Наш фильтр перегрева блокирует вход у максимумов —
  то есть отсекает ровно то, что работает.
* Волатильностная нормировка улучшает результат: «profit increases when
  volatility scaling is used».

Здесь всё это проверяется перебором. Главная опасность перебора —
подгонка: тысяча вариантов на одних данных даст «победителя» и на чистом
шуме. Поэтому история делится на обучение и проверку, лучший выбирается
ТОЛЬКО по обучению, а решение принимается по его результату на проверке,
которую он не видел.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional, Sequence

TRAIN_FRACTION = 0.70     # первые 70% истории — обучение, остальное проверка


@dataclass(frozen=True)
class Params:
    lookback: int          # окно моментума, дней
    holding: int           # максимальное удержание, дней
    vol_scaled: bool       # нормировать сигнал на волатильность
    long_only: bool        # только лонги


@dataclass(frozen=True)
class Result:
    params: Params
    n: int
    avg_r: Optional[float]
    total_r: float
    wr: Optional[float]


def _returns(closes: Sequence[float], i: int, lookback: int) -> Optional[float]:
    if i < lookback or i >= len(closes):
        return None
    prev = closes[i - lookback]
    return (closes[i] / prev - 1.0) if prev > 0 else None


def _vol(closes: Sequence[float], i: int, window: int = 30) -> Optional[float]:
    if i < window + 1:
        return None
    rets = [closes[k] / closes[k - 1] - 1.0
            for k in range(i - window + 1, i + 1) if closes[k - 1] > 0]
    return statistics.pstdev(rets) if len(rets) > 2 else None


def run_one(closes: Sequence[float], p: Params) -> list[float]:
    """Вернуть список R по сделкам для одного набора параметров.

    Модель намеренно простая: сигнал — знак доходности за окно; вход по
    закрытию; выход через holding дней или при смене знака. R меряется в
    единицах волатильности за 30 дней — это и есть нормировка риска,
    сопоставимая между монетами и периодами.
    """
    rs: list[float] = []
    start = max(p.lookback, 40)
    i = start
    while i < len(closes) - 1:
        mom = _returns(closes, i, p.lookback)
        vol = _vol(closes, i)
        if mom is None or not vol:
            i += 1
            continue
        signal = 1 if mom > 0 else -1
        if p.vol_scaled and abs(mom) < vol:
            i += 1                       # слабый относительно шума — мимо
            continue
        if p.long_only and signal < 0:
            i += 1
            continue

        entry = closes[i]
        exit_i = min(i + p.holding, len(closes) - 1)
        for k in range(i + 1, exit_i + 1):
            m = _returns(closes, k, p.lookback)
            if m is not None and (1 if m > 0 else -1) != signal:
                exit_i = k
                break
        move = (closes[exit_i] / entry - 1.0) * signal
        rs.append(move / vol)            # R в единицах суточной волатильности
        i = exit_i + 1
    return rs


def evaluate(closes: Sequence[float], p: Params) -> Result:
    rs = run_one(closes, p)
    if not rs:
        return Result(p, 0, None, 0.0, None)
    return Result(p, len(rs), statistics.mean(rs), sum(rs),
                  sum(1 for r in rs if r > 0) / len(rs))


def split(closes: Sequence[float]) -> tuple[list[float], list[float]]:
    cut = int(len(closes) * TRAIN_FRACTION)
    return list(closes[:cut]), list(closes[cut:])


def sweep(series: dict[str, Sequence[float]], grid: Sequence[Params]
          ) -> list[tuple[Params, Result, Result]]:
    """Перебрать сетку: обучение и проверка отдельно по каждому набору."""
    out = []
    for p in grid:
        tr_rs, te_rs = [], []
        for closes in series.values():
            train, test = split(closes)
            tr_rs.extend(run_one(train, p))
            te_rs.extend(run_one(test, p))

        def mk(rs):
            if not rs:
                return Result(p, 0, None, 0.0, None)
            return Result(p, len(rs), statistics.mean(rs), sum(rs),
                          sum(1 for r in rs if r > 0) / len(rs))

        out.append((p, mk(tr_rs), mk(te_rs)))
    return out


def default_grid() -> list[Params]:
    """Сетка вокруг того, что литература называет рабочим.

    Окна 7-90 дней (исследование указывает на 28), удержание 1-20
    (указывает на 5), с нормировкой и без, только лонг и обе стороны.
    """
    return [
        Params(lookback=lb, holding=h, vol_scaled=v, long_only=lo)
        for lb in (7, 14, 21, 28, 40, 60, 90)
        for h in (1, 3, 5, 10, 20)
        for v in (False, True)
        for lo in (False, True)
    ]
