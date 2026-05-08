"""Скоринг кандидатов из whitelist, фильтры и sizing.

Скоринг — взвешенная сумма сигналов из ТА и HL meta.
Фильтры — жёсткие правила, по которым токен исключается несмотря на скор.
"""
from __future__ import annotations

from typing import Any


def score_candidate(ind: dict, hl_ctx: dict, tier: int) -> float:
    """Скор кандидата. Чем выше — тем приоритетнее покупка.

    Веса калиброваны, не оптимизированы. Тюним по факту через decisions.jsonl.
    """
    s = 0.0

    # Momentum 30d (главный признак силы тренда)
    m30 = ind.get("momentum_30d")
    if m30 is not None:
        s += min(max(m30 / 10, -3), 3) * 1.0   # ±10% → ±1.0, capped

    # Momentum 7d (свежесть)
    m7 = ind.get("momentum_7d")
    if m7 is not None:
        s += min(max(m7 / 5, -2), 2) * 0.5

    # Цена vs EMA50 (короткий тренд)
    v50 = ind.get("vs_ema50_pct")
    if v50 is not None:
        s += min(max(v50 / 5, -2), 2) * 0.8

    # Цена vs EMA200 (длинный тренд) — самый важный для DCA
    v200 = ind.get("vs_ema200_pct")
    if v200 is not None:
        # Положительное расстояние — в плюс, но насыщение
        # т.к. слишком далеко от EMA200 = перегрев
        if v200 < 50:
            s += min(max(v200 / 10, -3), 3) * 1.2
        else:
            s += 6.0  # cap, дальше штраф через overheat-фильтр

    # RSI: 50 нейтрален, 60-70 здорово, выше — перегрев
    rsi = ind.get("rsi_d1")
    if rsi is not None:
        if 45 <= rsi <= 70:
            s += (rsi - 50) / 20 * 0.5    # 50→0, 70→0.5
        elif rsi > 70:
            s -= (rsi - 70) / 10 * 1.0    # 80 → −1.0

    # Funding penalty: чем выше positive funding, тем дороже long
    fapr = (hl_ctx or {}).get("funding_apr_pct")
    if fapr is not None and fapr > 0:
        s -= (fapr / 20) * 0.5            # 20% APR → −0.5

    # Tier penalty: T3 (narrative) — −0.5, T2 — 0, T1 — +0.3
    tier_adj = {1: 0.3, 2: 0.0, 3: -0.5}.get(tier, 0.0)
    s += tier_adj

    return round(s, 3)


def passes_filters(ind: dict, hl_ctx: dict, rules: dict, signal: str) -> tuple[bool, str | None]:
    """Возвращает (allowed, reason_if_blocked)."""
    if not hl_ctx:
        return False, "не листится на HL"

    # Funding-фильтр
    fapr = hl_ctx.get("funding_apr_pct")
    fapr_skip = rules.get("funding_apr_skip_above", 60)
    if fapr is not None and fapr > fapr_skip:
        return False, f"funding {fapr:+.0f}% APR > {fapr_skip}%"

    # RSI overheat
    rsi = ind.get("rsi_d1")
    rsi_max = rules.get("rsi_d1_overheated", 80)
    if rsi is not None and rsi > rsi_max:
        return False, f"RSI(D1) {rsi:.0f} > {rsi_max} (перегрет)"

    # Цена ниже EMA200 при STRONG-сигнале — медвежий ТА против бычьего регима
    if signal == "STRONG":
        if ind.get("above_ema200") is False:
            v200 = ind.get("vs_ema200_pct")
            return False, f"цена ниже EMA200 ({v200:+.1f}%)"

    # Не хватает истории для расчёта индикаторов
    if ind.get("ema200") is None:
        return False, "недостаточно истории для EMA200"

    return True, None


def calculate_sl(entry: float, ind: dict, rules: dict) -> dict:
    """SL = max(entry − 2×ATR, swing_low_20), но не дальше -hard_floor%."""
    atr = ind.get("atr14") or 0
    swing = ind.get("swing_low_20") or 0
    multiplier = rules.get("atr_multiplier", 2.0)
    floor_pct = rules.get("sl_hard_floor_pct", 20) / 100

    atr_sl = entry - multiplier * atr if atr else None
    floor_sl = entry * (1 - floor_pct)

    candidates = [v for v in (atr_sl, swing) if v and v < entry]
    if candidates:
        sl = max(candidates)            # ближе к entry, тише но реалистичнее
    else:
        sl = floor_sl
    sl = max(sl, floor_sl)              # но не дальше hard floor

    return {
        "sl_price": round(sl, 6),
        "sl_pct": round((sl - entry) / entry * 100, 2),
        "method": (
            "swing_low" if (swing and sl == swing)
            else "atr" if (atr_sl and abs(sl - atr_sl) < 1e-9)
            else "hard_floor"
        ),
    }


def allocate_budget(ranked: list[dict], signal: str, weekly_budget: float = 200.0) -> list[dict]:
    """Распределяет $200 по топ-кандидатам.

    STRONG: топ-1 = 60%, топ-2 = 40%
    MODERATE: топ-1 = 100%
    SKIP/EXIT: ничего
    """
    if signal == "STRONG":
        weights = [0.6, 0.4]
    elif signal == "MODERATE":
        weights = [1.0]
    else:
        return []

    out = []
    for cand, w in zip(ranked[: len(weights)], weights):
        out.append({**cand, "alloc_usd": round(weekly_budget * w, 2),
                    "alloc_pct": int(w * 100)})
    return out
