"""Бэктест тактического слоя по историческим свечам.

Заведён 01.09.2026. Живая выборка растёт по 28 сделок в месяц: до 400 —
одиннадцать месяцев, до 1000 — тридцать два. Внешняя практика: 100 сделок
минимум, 200 убедительно, 500 сильная значимость; зрелые проекты получают
сотни сделок за один прогон по истории, а живую торговлю используют уже
как проверку вне выборки. Мы делали наоборот.

Что здесь считается и чего здесь НЕТ.

Считается: та же функция вердикта, что работает в проде, прогоняется по
дневным свечам. Сделка открывается на смене вердикта по цене закрытия
свечи сигнала, стоп — тем же `sl_for`, что и в живом слое, выход по стопу,
цели или встречной смене вердикта.

Нет: комиссий, фандинга, проскальзывания, влияния на цену. Поэтому
результат — верхняя оценка, а не прогноз. Если слой не окупается ЗДЕСЬ,
в живой торговле он не окупится тем более.

Look-ahead: вход берётся по закрытию свечи, на которой возник сигнал;
исход проверяется начиная со СЛЕДУЮЩЕЙ свечи. Внутри свечи при касании и
стопа, и цели консервативно засчитывается стоп.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass
from typing import Optional, Sequence

# Вердикт требует >= 200 закрытий (EMA200). Меньше — он возвращает WAIT.
MIN_HISTORY = 210
TARGET_R = 1.5            # та же цель, что в тактическом сигнале


@dataclass(frozen=True)
class Trade:
    coin: str
    direction: str
    entry_idx: int
    exit_idx: int
    entry: float
    sl: float
    tp: float
    exit_px: float
    exit_reason: str
    r: float


def _r_multiple(direction: str, entry: float, sl: float, exit_px: float) -> float:
    risk = abs(entry - sl)
    if risk <= 0:
        return 0.0
    move = (exit_px - entry) if direction == "LONG" else (entry - exit_px)
    return move / risk


def _verdict_at(coin: str, candles: Sequence[dict], i: int) -> Optional[str]:
    """Вердикт на закрытии свечи i — тем же кодом, что в проде."""
    from src.eth_focus import compute_verdict_pair
    from src.ta import compute_indicators

    closes = [float(c["c"]) for c in candles[: i + 1]]
    if len(closes) < 200:
        return None
    try:
        # Свечи строятся из закрытий ровно так же, как в проде
        # (whitelist_focus.evaluate_coin_pair). Это воспроизведение
        # РАЗВЁРНУТОЙ логики, а не улучшенной: бэктест обязан мерить то,
        # что работает, иначе его результат не о нашей системе.
        cd = [{"o": c, "h": c, "l": c, "c": c} for c in closes]
        ta_dict = compute_indicators(cd, swing_lookback=30)
        _raw, final = compute_verdict_pair(
            ta=ta_dict, funding_apr_pct=None, whale_net_long=None,
            whale_cluster_count=0, regime=None, phase=None,
        )
    except Exception:  # noqa: BLE001
        return None
    return final[0] if isinstance(final, tuple) else final


def replay(coin: str, candles: Sequence[dict]) -> list[Trade]:
    """Прогнать логику по свечам и вернуть виртуальные сделки."""
    from src.tactical_signals import sl_for
    from src import ta

    if len(candles) <= MIN_HISTORY + 2:
        return []

    trades: list[Trade] = []
    open_trade: Optional[dict] = None
    prev_verdict: Optional[str] = None

    for i in range(MIN_HISTORY, len(candles) - 1):
        price = float(candles[i]["c"])
        verdict = _verdict_at(coin, candles, i)

        # выход по стопу или цели — со следующей свечи
        if open_trade:
            nxt = candles[i + 1]
            hi, lo = float(nxt["h"]), float(nxt["l"])
            d, sl, tp = open_trade["direction"], open_trade["sl"], open_trade["tp"]
            hit_sl = lo <= sl if d == "LONG" else hi >= sl
            hit_tp = hi >= tp if d == "LONG" else lo <= tp
            reason = px = None
            if hit_sl:                       # консервативно: стоп раньше цели
                reason, px = "sl", sl
            elif hit_tp:
                reason, px = "tp", tp
            elif verdict and verdict != d:
                reason, px = "verdict_flip", price
            if reason:
                trades.append(Trade(
                    coin=coin, direction=d,
                    entry_idx=open_trade["idx"], exit_idx=i + 1,
                    entry=open_trade["entry"], sl=sl, tp=tp,
                    exit_px=px, exit_reason=reason,
                    r=_r_multiple(d, open_trade["entry"], sl, px)))
                open_trade = None

        # вход на смене вердикта
        if (not open_trade and verdict in ("LONG", "SHORT")
                and verdict != prev_verdict):
            window = candles[max(0, i - 30): i + 1]
            atr = ta.atr(candles[: i + 1], 14)
            lows = [float(c["l"]) for c in window]
            highs = [float(c["h"]) for c in window]
            sl = sl_for(verdict, price, atr, min(lows), max(highs))
            if sl and sl > 0 and ((verdict == "LONG" and sl < price)
                                  or (verdict == "SHORT" and sl > price)):
                risk = abs(price - sl)
                tp = (price + TARGET_R * risk if verdict == "LONG"
                      else price - TARGET_R * risk)
                open_trade = {"direction": verdict, "idx": i,
                              "entry": price, "sl": sl, "tp": tp}
        prev_verdict = verdict

    return trades


def _bootstrap_ci(values: Sequence[float], iterations: int = 2000,
                  seed: int = 20260901) -> tuple[Optional[float], Optional[float]]:
    if len(values) < 2:
        return None, None
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n
                   for _ in range(iterations))
    return means[int(0.025 * len(means))], means[int(0.975 * len(means)) - 1]


def summarise(trades: Sequence[Trade]) -> dict:
    if not trades:
        return {"n": 0, "avg_r": None, "median_r": None, "wr": None,
                "ci": (None, None), "by_reason": {}, "by_side": {}}

    rs = [t.r for t in trades]
    lo, hi = _bootstrap_ci(rs)
    by_reason: dict[str, int] = {}
    for t in trades:
        by_reason[t.exit_reason] = by_reason.get(t.exit_reason, 0) + 1
    by_side = {}
    for side in ("LONG", "SHORT"):
        side_rs = [t.r for t in trades if t.direction == side]
        if side_rs:
            by_side[side] = {"n": len(side_rs),
                             "avg_r": statistics.mean(side_rs)}
    return {
        "n": len(trades),
        "avg_r": statistics.mean(rs),
        "median_r": statistics.median(rs),
        "wr": sum(1 for r in rs if r > 0) / len(rs),
        "ci": (lo, hi),
        "by_reason": by_reason,
        "by_side": by_side,
    }
