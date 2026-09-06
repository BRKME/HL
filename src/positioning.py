"""Позиционирование толпы: сбор и проверка предсказательной силы.

Единственный класс данных, которого у нас нет: цены, свечи, объёмы и
сделки китов доступны всем и всеми обработаны. Hyperliquid таких срезов не
отдаёт, Binance и Bybit блокируют раннеры Actions по региону, OKX отдаёт.

Что даёт OKX (проверено пробой 04.09):
  счета        180 суточных точек, 11.03 → 06.09
  топ-позиции  100 суточных точек, 30.05 → 06.09
Формат — массивы ['метка', 'отношение']: одно число, отношение лонгов к
шортам. Долей longAccount/shortAccount нет, их надо выводить из отношения.

Собираем РАЗ В СУТКИ, а не в час: за час позиционирование толпы не меняется
осмысленно, и часовые точки дали бы 24 почти одинаковых числа в день — та
же передискретизация, что ловили у китов и в журнале.

Главное здесь — не уровень, а РАСХОЖДЕНИЕ между срезами: отношение по
счетам против отношения по позициям топ-трейдеров. Толпа и крупные деньги
расходятся именно в этом, и готового такого показателя ни у кого нет.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional, Sequence

OKX_ACCOUNTS = ("https://www.okx.com/api/v5/rubik/stat/contracts/"
                "long-short-account-ratio")
OKX_TOP_POS = ("https://www.okx.com/api/v5/rubik/stat/contracts/"
               "long-short-position-ratio-contract-top-trader")


@dataclass(frozen=True)
class Point:
    ts_ms: int
    accounts_ratio: Optional[float]      # лонги/шорты по числу счетов
    top_pos_ratio: Optional[float]       # лонги/шорты по размеру у топов

    @property
    def divergence(self) -> Optional[float]:
        """Насколько крупные деньги расходятся с толпой.

        Положительно — топы длиннее толпы, отрицательно — короче. Именно
        это расхождение оператор и заметил в статистике по ZEC: 76% счетов
        в шорте против почти нейтральных позиций у топов.
        """
        if self.accounts_ratio is None or self.top_pos_ratio is None:
            return None
        return self.top_pos_ratio - self.accounts_ratio


def long_share(ratio: Optional[float]) -> Optional[float]:
    """Доля лонгов из отношения: r/(1+r). Обратно к тому, что даёт OKX."""
    if ratio is None or ratio < 0:
        return None
    return ratio / (1.0 + ratio)


def parse_okx(rows: Sequence) -> dict[int, float]:
    """OKX отдаёт массивы ['1788710400000', '1.06'], а не словари."""
    out: dict[int, float] = {}
    for r in rows or []:
        try:
            if isinstance(r, dict):
                ts, val = r.get("ts"), r.get("ratio")
            else:
                ts, val = r[0], r[1]
            if ts is None or val is None:
                continue
            out[int(ts)] = float(val)
        except (TypeError, ValueError, IndexError):
            continue
    return out


def merge(accounts: dict[int, float], top_pos: dict[int, float]
          ) -> list[Point]:
    keys = sorted(set(accounts) | set(top_pos))
    return [Point(k, accounts.get(k), top_pos.get(k)) for k in keys]


# ------------------------------------------------- проверка предсказания

@dataclass(frozen=True)
class Signal:
    label: str
    n: int
    avg_fwd_pct: Optional[float]
    median_fwd_pct: Optional[float]
    win_rate: Optional[float]


def _pct_rank(values: Sequence[float], x: float) -> float:
    if not values:
        return 0.5
    return sum(1 for v in values if v <= x) / len(values)


def forward_returns(points: Sequence[Point], prices: dict[int, float],
                    horizon_d: int = 1) -> list[tuple[Point, float]]:
    """Доходность следующих дней после каждой точки.

    Суточный горизонт выбран потому, что наблюдения при нём почти не
    перекрываются: доходность завтрашнего дня не пересекается с
    послезавтрашней. При семидневном из 179 точек осталось бы около 25
    независимых — ниже нашего порога.
    """
    day = 86_400_000
    out = []
    for p in points:
        p0 = prices.get(p.ts_ms)
        p1 = prices.get(p.ts_ms + horizon_d * day)
        if p0 and p1 and p0 > 0:
            out.append((p, (p1 / p0 - 1.0) * 100))
    return out


def evaluate(pairs: Sequence[tuple[Point, float]],
             extreme_pct: float = 0.20) -> list[Signal]:
    """Сравнить доходность после экстремального позиционирования с базой.

    Экстремум считается по СОБСТВЕННОЙ истории монеты, а не по абсолютному
    уровню: «отношение 0.35» само по себе ничего не значит, значение имеет
    отклонение от нормы этой монеты.
    """
    if not pairs:
        return []

    acc = [p.accounts_ratio for p, _ in pairs if p.accounts_ratio is not None]
    div = [p.divergence for p, _ in pairs if p.divergence is not None]

    def group(pred, label):
        rets = [r for p, r in pairs if pred(p)]
        if not rets:
            return Signal(label, 0, None, None, None)
        return Signal(label, len(rets), statistics.mean(rets),
                      statistics.median(rets),
                      sum(1 for r in rets if r > 0) / len(rets))

    out = [group(lambda p: True, "все дни")]
    if acc:
        out.append(group(
            lambda p: (p.accounts_ratio is not None
                       and _pct_rank(acc, p.accounts_ratio) <= extreme_pct),
            "толпа в шорте (низ 20%)"))
        out.append(group(
            lambda p: (p.accounts_ratio is not None
                       and _pct_rank(acc, p.accounts_ratio) >= 1 - extreme_pct),
            "толпа в лонге (верх 20%)"))
    if div:
        out.append(group(
            lambda p: (p.divergence is not None
                       and _pct_rank(div, p.divergence) >= 1 - extreme_pct),
            "топы длиннее толпы (верх 20%)"))
        out.append(group(
            lambda p: (p.divergence is not None
                       and _pct_rank(div, p.divergence) <= extreme_pct),
            "топы короче толпы (низ 20%)"))
    return out
