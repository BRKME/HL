"""Премия за волатильность: подразумеваемая минус реализованная.

Заведено 19.09.2026, второй этап. Отвечает на единственный вопрос
политики по опционам: какая из двух премий перевешивает — за риск актива
(в пользу покупателя) или за волатильность (в пользу продавца).

Источник подразумеваемой — стакан Aevo: третье поле уровня. Реализованная
считается по свечам самим, оценкой Паркинсона: она использует максимум и
минимум дня и потому втрое точнее оценки по закрытиям при том же числе
наблюдений.

Чего здесь нет и не будет: вывода «значит, надо продавать». Спред на Aevo
составляет 9.9 пункта волатильности по биткоину — пересечь его туда и
обратно дороже всей задокументированной премии. Замер отвечает,
существует ли преимущество, а не где его брать.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Optional, Sequence

TRADING_DAYS = 365          # крипта торгуется без выходных
MIN_OBSERVATIONS = 20       # ниже — оценка волатильности не имеет смысла


@dataclass(frozen=True)
class PremiumPoint:
    ts_ms: int
    asset: str
    implied: float          # доля, не проценты: 0.35 = 35%
    realized: float
    horizon_days: int

    @property
    def premium_pp(self) -> float:
        """Премия в процентных ПУНКТАХ — в них она и обсуждается."""
        return (self.implied - self.realized) * 100


def parkinson_volatility(candles: Sequence[dict], window: int = 30
                         ) -> Optional[float]:
    """Годовая реализованная волатильность по максимумам и минимумам.

    Оценка по закрытиям выбрасывает внутридневной ход: день, сходивший на
    5% вверх и вернувшийся, засчитывается как нулевой. Паркинсон этого не
    теряет и потому точнее при том же числе наблюдений — а наблюдений у
    нас мало по определению.
    """
    rows = []
    for c in candles or []:
        if not isinstance(c, dict):
            continue
        try:
            hi, lo = float(c["h"]), float(c["l"])
        except (KeyError, TypeError, ValueError):
            continue
        if hi > 0 and lo > 0:
            rows.append((hi, lo))
    if len(rows) < MIN_OBSERVATIONS:
        return None

    rows = rows[-window:]
    k = 1.0 / (4.0 * math.log(2.0))
    acc = sum(k * math.log(hi / lo) ** 2 for hi, lo in rows)
    return math.sqrt(acc / len(rows) * TRADING_DAYS)


def close_to_close_volatility(candles: Sequence[dict], window: int = 30
                              ) -> Optional[float]:
    """Классическая оценка — для сверки с Паркинсоном.

    Две оценки, сильно расходящиеся между собой, означают, что данные
    подозрительны, а не что одна из них лучше.
    """
    closes = []
    for c in candles or []:
        if isinstance(c, dict) and c.get("c"):
            try:
                closes.append(float(c["c"]))
            except (TypeError, ValueError):
                continue
    if len(closes) < MIN_OBSERVATIONS + 1:
        return None
    closes = closes[-(window + 1):]
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0]
    if len(rets) < 2:
        return None
    return statistics.pstdev(rets) * math.sqrt(TRADING_DAYS)


def atm_implied(levels_bid: Sequence, levels_ask: Sequence) -> Optional[float]:
    """Подразумеваемая волатильность по середине стакана.

    Берётся середина между бидом и аском, а не одна сторона: на Aevo
    разрыв достигает 9.9 пункта по биткоину, и выбор любой стороны сдвинет
    замер на половину спреда.
    """
    def _iv(levels):
        for lv in levels or []:
            try:
                if len(lv) >= 3 and float(lv[2]) > 0:
                    return float(lv[2])
            except (TypeError, ValueError, IndexError):
                continue
        return None

    b, a = _iv(levels_bid), _iv(levels_ask)
    if b is None and a is None:
        return None
    if b is None or a is None:
        return b if b is not None else a
    return (b + a) / 2


def summarise(points: Sequence[PremiumPoint]) -> dict:
    """Сводка по премии. Отсутствие данных не превращается в ноль."""
    if not points:
        return {"n": 0, "mean_pp": None, "median_pp": None,
                "positive_share": None, "worst_pp": None}
    vals = [p.premium_pp for p in points]
    return {
        "n": len(vals),
        "mean_pp": statistics.mean(vals),
        "median_pp": statistics.median(vals),
        "positive_share": sum(1 for v in vals if v > 0) / len(vals),
        "worst_pp": min(vals),
    }
