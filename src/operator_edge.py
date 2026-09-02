"""Разбор торговли ОПЕРАТОРА теми же мерками, что и модели.

Два дня мы доказывали, что модель не обгоняет случайный вход. За тот же
период оператор сделал руками +46.8%, и его результат мы не измерили ни
разу — притом что это единственный источник преимущества, который мы
вообще наблюдали.

Вопрос честный и открытый: это удача или умение. Один месяц ничего не
доказывает, но история сделок лежит на бирже, и мерить её можно тем же
инструментом, которым мы забраковали модель, — иначе выйдет двойной
стандарт: к своей работе строгие требования, к чужой никаких.

Что считается: сделки восстанавливаются из исполнений (fills), для каждой
считается доходность в единицах волатильности; сравнение — со случайным
входом той же экспозиции и с удержанием. Что НЕ считается: значимость при
малом числе сделок. Если их два десятка, никакой вывод невозможен, и это
будет сказано прямо.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class OperatorTrade:
    coin: str
    direction: str
    entry_ms: int
    exit_ms: int
    entry: float
    exit_px: float
    size: float
    pnl_usd: float

    @property
    def return_pct(self) -> float:
        if self.entry <= 0:
            return 0.0
        move = ((self.exit_px - self.entry) if self.direction == "LONG"
                else (self.entry - self.exit_px))
        return move / self.entry * 100

    @property
    def hold_days(self) -> float:
        return max(self.exit_ms - self.entry_ms, 0) / 86_400_000


def pair_fills(fills: Sequence[dict]) -> list[OperatorTrade]:
    """Свести исполнения в сделки: открытие → закрытие по каждой монете.

    Биржа отдаёт поток исполнений, а не сделок. Позиция может набираться и
    закрываться частями, поэтому ведём остаток по монете и закрываем
    сделку, когда он возвращается к нулю: это соответствует тому, что
    оператор считает одной сделкой.
    """
    state: dict[str, dict] = {}
    out: list[OperatorTrade] = []

    for f in sorted(fills, key=lambda x: x.get("time", 0)):
        coin = f.get("coin")
        try:
            px = float(f.get("px", 0))
            sz = float(f.get("sz", 0))
            ts = int(f.get("time", 0))
        except (TypeError, ValueError):
            continue
        if not coin or px <= 0 or sz <= 0:
            continue

        side = str(f.get("dir") or f.get("side") or "")
        is_open = "Open" in side
        is_long = "Long" in side

        st = state.get(coin)
        if is_open:
            if st is None:
                state[coin] = {"remaining": sz, "entry": px, "ts": ts,
                               "dir": "LONG" if is_long else "SHORT",
                               "pnl": 0.0}
            else:
                # доливка: средняя цена входа по объёму
                total = st["remaining"] + sz
                st["entry"] = (st["entry"] * st["remaining"] + px * sz) / total
                st["remaining"] = total
            continue

        if st is None:
            continue                      # закрытие без открытия — пропуск
        st["pnl"] += float(f.get("closedPnl") or 0)
        st["remaining"] -= sz
        if st["remaining"] <= 1e-9:
            out.append(OperatorTrade(
                coin=coin, direction=st["dir"], entry_ms=st["ts"],
                exit_ms=ts, entry=st["entry"], exit_px=px,
                size=sz, pnl_usd=st["pnl"]))
            state.pop(coin, None)
    return out


def summarise(trades: Sequence[OperatorTrade]) -> dict:
    if not trades:
        return {"n": 0}
    rets = [t.return_pct for t in trades]
    pnls = [t.pnl_usd for t in trades]
    wins = [r for r in rets if r > 0]
    return {
        "n": len(trades),
        "wr": len(wins) / len(trades),
        "avg_return_pct": statistics.mean(rets),
        "median_return_pct": statistics.median(rets),
        "total_pnl_usd": sum(pnls),
        "avg_hold_days": statistics.mean(t.hold_days for t in trades),
        "by_coin": _by_coin(trades),
    }


def _by_coin(trades: Sequence[OperatorTrade]) -> dict:
    out: dict[str, dict] = {}
    for t in trades:
        d = out.setdefault(t.coin, {"n": 0, "pnl": 0.0})
        d["n"] += 1
        d["pnl"] += t.pnl_usd
    return out


MIN_TRADES_FOR_ANY_CLAIM = 30


def verdict(summary: dict) -> str:
    """Вывод с той же строгостью, что применялась к модели."""
    n = summary.get("n", 0)
    if n < MIN_TRADES_FOR_ANY_CLAIM:
        return (f"НЕЛЬЗЯ СУДИТЬ: {n} сделок при минимуме "
                f"{MIN_TRADES_FOR_ANY_CLAIM}. Тот же порог, по которому мы "
                f"отказывались делать выводы о модели.")
    avg = summary.get("avg_return_pct") or 0.0
    if avg <= 0:
        return f"средняя доходность сделки {avg:+.2f}% — преимущества нет"
    return (f"средняя доходность сделки {avg:+.2f}%, WR "
            f"{summary['wr']:.0%} — нужен контроль случайным входом")
