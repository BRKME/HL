"""Оценка исходов тактических сигналов — KPI-слой тактики.

Сигнал без измеренного исхода — это шум с хорошим интерфейсом. Каждый
эмитированный (и подавленный фильтрами — shadow) сигнал пишется в
state/tactical_journal.jsonl, а этот модуль превращает записи в R-мультипли:

  first-touch по дневным свечам, горизонт EVAL_HORIZON_DAYS:
    LONG : low<=SL раньше -> −1R; high>=entry+2R раньше -> +2R;
           обе границы в одной свече -> консервативно −1R;
           ни одна за горизонт -> частичный R=(close−entry)/R (open_expired).
    SHORT: зеркально.

Агрегация с ограждениями честности: закрытых исходов < MIN_DECIDED ->
«рано судить» без процентов; каждая цифра с n.
"""
from __future__ import annotations

from typing import Dict, List, Optional

EVAL_HORIZON_DAYS = 7
TARGET_R = 2.0          # цель +2R — фиксированный контракт оценки
MIN_DECIDED = 5         # меньше закрытых — рано судить


def evaluate_signal(direction: str, entry: float, sl: Optional[float],
                    candles: List[dict]) -> Dict:
    """Исход одного сигнала по свечам, начиная со свечи входа.

    candles: [{'h','l','c'}, ...] — дневные, первая = день сигнала.
    """
    if not sl or not entry or entry <= 0:
        return {"status": "unevaluable", "r_multiple": None}
    risk = abs(entry - sl)
    if risk <= 0:
        return {"status": "unevaluable", "r_multiple": None}

    horizon = candles[:EVAL_HORIZON_DAYS]
    if direction == "LONG":
        target = entry + TARGET_R * risk
        for c in horizon:
            hit_sl = float(c["l"]) <= sl
            hit_tg = float(c["h"]) >= target
            if hit_sl:                   # включая неоднозначную свечу — worst
                return {"status": "loss", "r_multiple": -1.0}
            if hit_tg:
                return {"status": "win", "r_multiple": TARGET_R}
        if len(candles) < EVAL_HORIZON_DAYS:
            return {"status": "pending", "r_multiple": None}
        close = float(horizon[-1]["c"])
        return {"status": "open_expired",
                "r_multiple": round((close - entry) / risk, 4)}

    if direction == "SHORT":
        target = entry - TARGET_R * risk
        for c in horizon:
            hit_sl = float(c["h"]) >= sl
            hit_tg = float(c["l"]) <= target
            if hit_sl:
                return {"status": "loss", "r_multiple": -1.0}
            if hit_tg:
                return {"status": "win", "r_multiple": TARGET_R}
        if len(candles) < EVAL_HORIZON_DAYS:
            return {"status": "pending", "r_multiple": None}
        close = float(horizon[-1]["c"])
        return {"status": "open_expired",
                "r_multiple": round((entry - close) / risk, 4)}

    return {"status": "unevaluable", "r_multiple": None}


def aggregate(rows: List[Dict]) -> Dict:
    """KPI-строка по оценённым сигналам. Честность: n всегда рядом."""
    decided = [r for r in rows if r.get("status") in ("win", "loss")]
    scored = [r for r in rows
              if r.get("r_multiple") is not None
              and r.get("status") in ("win", "loss", "open_expired")]
    n_dec = len(decided)
    if n_dec < MIN_DECIDED:
        return {"verdict_ready": False,
                "line": (f"Тактика: закрытых исходов {n_dec}/{MIN_DECIDED} — "
                         f"рано судить (сигналы зреют ~{EVAL_HORIZON_DAYS}д)")}
    wins = sum(1 for r in decided if r["status"] == "win")
    wr = wins / n_dec * 100
    avg_r = sum(float(r["r_multiple"]) for r in scored) / len(scored)
    by_dir = {}
    for r in scored:
        d = r.get("direction", "?")
        by_dir.setdefault(d, []).append(float(r["r_multiple"]))
    dir_bits = " · ".join(
        f"{d}: avgR {sum(v)/len(v):+.2f} (n={len(v)})"
        for d, v in sorted(by_dir.items()))
    line = (f"Тактика: n={n_dec} закрытых · WR {wr:.0f}% · "
            f"avg R {avg_r:+.2f} (по {len(scored)} оценённым)")
    if dir_bits:
        line += f"\n     {dir_bits}"
    return {"verdict_ready": True, "line": line}
