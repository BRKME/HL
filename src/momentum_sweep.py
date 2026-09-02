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
    ma_len: int = 0        # режимный фильтр по средней; 0 = выключен
    ma_buffer: float = 0.0  # зона нечувствительности вокруг средней


@dataclass(frozen=True)
class Result:
    params: Params
    n: int
    avg_r: Optional[float]
    total_r: float
    wr: Optional[float]


# ------------------------------------------------ режимный фильтр по средней

# Ширина зоны нечувствительности вокруг средней, доля от её значения.
# Замечание оператора 02.09: у самой линии случаются ложные пробои. Цена,
# болтающаяся вокруг средней, без зоны даёт серию переключений подряд —
# каждое из них стоит комиссии и портит выборку. Внутри зоны состояние
# СОХРАНЯЕТСЯ, а не пересчитывается: это гистерезис, тот же приём, что мы
# применили к verdict_flip в гварде.
REGIME_BUFFERS = (0.0, 0.02, 0.04)
REGIME_MA_LENGTHS = (0, 100, 200)      # 0 = фильтр выключен


def moving_average(closes: Sequence[float], i: int, length: int
                   ) -> Optional[float]:
    if length <= 0 or i + 1 < length:
        return None
    window = closes[i + 1 - length: i + 1]
    return sum(window) / len(window)


def regime_series(closes: Sequence[float], ma_len: int,
                  buffer_pct: float) -> list[Optional[bool]]:
    """Для каждой свечи: True — «выше средней», False — «ниже», None — рано.

    Внутри зоны нечувствительности состояние наследуется от предыдущего
    дня. Именно это отсекает ложные пробои: чтобы режим сменился, цене
    надо уйти за границу зоны, а не просто коснуться линии.
    """
    out: list[Optional[bool]] = []
    state: Optional[bool] = None
    for i in range(len(closes)):
        ma = moving_average(closes, i, ma_len)
        if ma is None or ma <= 0:
            out.append(None)
            continue
        upper, lower = ma * (1 + buffer_pct), ma * (1 - buffer_pct)
        price = closes[i]
        if price > upper:
            state = True
        elif price < lower:
            state = False
        # внутри зоны — состояние не меняется
        out.append(state)
    return out


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
    # Режимная разметка считается один раз на всю серию: пересчёт внутри
    # цикла давал бы то же самое, но медленнее.
    regime = (regime_series(closes, p.ma_len, p.ma_buffer)
              if p.ma_len else None)
    start = max(p.lookback, 40, p.ma_len)
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
        # Режимный фильтр: лонг только выше средней, шорт только ниже.
        if regime is not None:
            state = regime[i]
            if state is None or (state and signal < 0) or \
                    (not state and signal > 0):
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


# ------------------------------------------- сравнение с «купи и держи»

def buy_and_hold_r(closes: Sequence[float]) -> Optional[float]:
    """Доходность «купи и держи» в тех же единицах, что и сделки модели.

    Без этого числа результат перебора не значит ничего. Стратегия «только
    лонг с удержанием 20 дней» асимптотически превращается в «купи и
    держи»: если проверочный период был растущим, она покажет прибыль без
    какого-либо предсказания. Ровно на этом мы уже обожглись с альфой
    недельного планнера — измерили направленную стратегию на направленном
    рынке и приняли бету за преимущество.
    """
    if len(closes) < 40:
        return None
    vol = _vol(closes, len(closes) - 1)
    if not vol:
        return None
    total = closes[-1] / closes[0] - 1.0
    return total / vol


def time_in_market(closes: Sequence[float], p: Params) -> Optional[float]:
    """Доля времени, проведённого в позиции.

    Близкая к единице означает, что «стратегия» это и есть рынок.
    """
    if len(closes) < 40:
        return None
    days_in = 0
    start = max(p.lookback, 40)
    i = start
    while i < len(closes) - 1:
        mom = _returns(closes, i, p.lookback)
        vol = _vol(closes, i)
        if mom is None or not vol:
            i += 1
            continue
        signal = 1 if mom > 0 else -1
        if (p.vol_scaled and abs(mom) < vol) or (p.long_only and signal < 0):
            i += 1
            continue
        exit_i = min(i + p.holding, len(closes) - 1)
        for k in range(i + 1, exit_i + 1):
            m = _returns(closes, k, p.lookback)
            if m is not None and (1 if m > 0 else -1) != signal:
                exit_i = k
                break
        days_in += exit_i - i
        i = exit_i + 1
    span = len(closes) - start
    return days_in / span if span > 0 else None


def random_entry_r(closes: Sequence[float], holding: int, exposure: float,
                   long_only: bool = True, seed: int = 20260901,
                   iterations: int = 400) -> Optional[float]:
    """Средний R случайных входов с ТОЙ ЖЕ долей времени в рынке.

    Правильный контроль, которого не даёт «купи и держи». Сравнение с
    удержанием обманывает дважды и в обе стороны:

    * на растущем рынке модель, сидящая в позиции половину времени,
      проиграет удержанию просто потому, что была вне рынка;
    * на падающем — выиграет по той же причине, ничего не предсказав.
      Ровно так «альфа» недельного планнера перевернулась с +20 на −20,
      когда рынок сменил направление.

    Случайные входы с той же экспозицией снимают оба искажения: остаётся
    только вопрос, есть ли в модели умение ВЫБИРАТЬ момент.
    """
    import random as _r

    if len(closes) < 60 or holding < 1 or not 0 < exposure <= 1:
        return None
    rng = _r.Random(seed)
    start = 40
    span = len(closes) - start - holding - 1
    if span <= 0:
        return None
    n_entries = max(1, int(span * exposure / holding))

    totals = []
    for _ in range(iterations):
        rs = []
        for _ in range(n_entries):
            i = start + rng.randrange(span)
            vol = _vol(closes, i)
            if not vol:
                continue
            j = min(i + holding, len(closes) - 1)
            side = 1 if long_only else rng.choice((1, -1))
            rs.append((closes[j] / closes[i] - 1.0) * side / vol)
        if rs:
            totals.append(statistics.mean(rs))
    return statistics.mean(totals) if totals else None


def equity_curve(closes: Sequence[float], p: Params) -> list[float]:
    """Кривая накопленного R по сделкам модели — для просадки."""
    rs = run_one(closes, p)
    curve, acc = [], 0.0
    for r in rs:
        acc += r
        curve.append(acc)
    return curve


def max_drawdown(curve: Sequence[float]) -> Optional[float]:
    """Максимальная просадка кривой, в тех же единицах R.

    Для оператора на плече 5x просадка важнее доходности: стратегия с
    меньшей доходностью и вдвое меньшей просадкой допускает вдвое большее
    плечо при том же риске, то есть даёт БОЛЬШЕ денег. Сравнение по одной
    доходности систематически выбирает то, что нельзя торговать с плечом.
    """
    if not curve:
        return None
    peak, worst = curve[0], 0.0
    for x in curve:
        peak = max(peak, x)
        worst = min(worst, x - peak)
    return worst


def buy_and_hold_curve(closes: Sequence[float]) -> list[float]:
    """«Купи и держи» в тех же единицах R — для сопоставимой просадки."""
    if len(closes) < 40:
        return []
    vol = _vol(closes, len(closes) - 1)
    if not vol:
        return []
    return [(c / closes[0] - 1.0) / vol for c in closes]


def walk_forward(closes: Sequence[float], grid: Sequence[Params],
                 folds: int = 4, with_drawdown: bool = False):
    """Скользящая проверка: обучаемся на прошлом, торгуем следующий отрезок.

    Разделение 70/30 отвечает на вопрос «работало ли это в конце периода».
    Скользящая проверка отвечает на другой, более важный: работало ли оно
    ПОВТОРЯЕМО — то есть выдержит ли смену режима, которой мы ещё не
    видели.

    Каждый отрезок: выбрать лучший набор ТОЛЬКО по предыдущим данным, затем
    измерить его на следующем отрезке, которого он не видел. Возвращается
    список (выбранный набор, результат на обучении, результат вперёд).

    Если лучший набор скачет от отрезка к отрезку, а результаты вперёд
    около нуля — устойчивого преимущества нет, как бы хорош ни был один
    удачный сплит.
    """
    n = len(closes)
    if n < 300 or folds < 2:
        return []
    out = []
    step = n // (folds + 1)
    for f in range(1, folds + 1):
        train_end = step * f
        test_end = min(step * (f + 1), n)
        train, test = closes[:train_end], closes[train_end:test_end]
        if len(train) < 150 or len(test) < 40:
            continue
        scored = []
        for p in grid:
            rs = run_one(train, p)
            if len(rs) >= 5:
                scored.append((statistics.mean(rs), p))
        if not scored:
            continue
        scored.sort(reverse=True, key=lambda x: x[0])
        train_r, best_p = scored[0]
        fwd = run_one(test, best_p)
        fwd_mean = statistics.mean(fwd) if fwd else 0.0
        if not with_drawdown:
            out.append((best_p, train_r, fwd_mean))
            continue

        # Доходность на единицу просадки — на КАЖДОМ отрезке, а не на
        # одном удачном сплите. Преимущество по просадке структурное:
        # модель вне рынка половину времени, — и может держаться там, где
        # доходность неустойчива. Именно это и надо проверить отдельно.
        curve, acc = [], 0.0
        for r in fwd:
            acc += r
            curve.append(acc)
        dd_m = max_drawdown(curve)
        dd_b = max_drawdown(buy_and_hold_curve(test))
        bh_r = buy_and_hold_r(test)
        ratio_m = (sum(fwd) / abs(dd_m)) if (fwd and dd_m) else None
        ratio_b = (bh_r / abs(dd_b)) if (bh_r is not None and dd_b) else None
        out.append((best_p, train_r, fwd_mean, ratio_m, ratio_b))
    return out
