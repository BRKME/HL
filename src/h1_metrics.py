"""Метрики и решающие правила гипотезы H1 — дребезг режима.

Гипотеза и пороги зарегистрированы в docs/OPERATING_POLICY.md §3 08.08.2026,
до сбора данных. Здесь они только исполняются. Пороги вынесены в константы
и менять их задним числом — значит отменять смысл предрегистрации: если
результат заставит их пересмотреть, это отдельное решение оператора,
записываемое в политику как новая гипотеза, а не правка этих чисел.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

MIN_SAMPLE = 20              # выходов на policy_version=2
SHARE_THRESHOLD = 0.30       # доля regime_flip, выше которой смотрим дальше
REENTRY_THRESHOLD = 0.40     # доля возвратов внутри окна
REENTRY_WINDOW_H = 24
MIN_REGIME_FLIPS_30D = 4     # меньше — период считаем всплеском


@dataclass(frozen=True)
class H1Result:
    n_exits: int
    regime_flip_share: float
    reentry_rate: float
    regime_flip_median_r: Optional[float]
    regime_flips_30d: int


def _ts(row: dict) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(row.get("ts", ""))
    except (ValueError, TypeError):
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


def reentry_rate_after(rows: Sequence[dict], reason: str,
                       window_h: int = REENTRY_WINDOW_H) -> float:
    """Доля выходов, за которыми в окне последовал вход в ту же сторону.

    Возврат в ту же сторону по той же монете — это дребезг: позицию закрыли
    и открыли обратно. Разворот в противоположную сторону дребезгом не
    считается, это смена решения.
    """
    exits = [r for r in rows if r.get("exit_reason") == reason]
    if not exits:
        return 0.0

    entries = [r for r in rows
               if not r.get("exit_reason")
               and r.get("direction") in ("LONG", "SHORT")]

    hits = 0
    for e in exits:
        t0 = _ts(e)
        if t0 is None:
            continue
        side = str(e.get("closed_direction", "")).upper()
        for n in entries:
            t1 = _ts(n)
            if (t1 is not None and n.get("coin") == e.get("coin")
                    and str(n.get("direction", "")).upper() == side
                    and t0 < t1 <= t0 + timedelta(hours=window_h)):
                hits += 1
                break
    return hits / len(exits)


def compute_h1(rows: Sequence[dict], now: datetime,
               regime_changes_30d: int) -> H1Result:
    p2 = [r for r in rows
          if r.get("exit_reason") and r.get("policy_version") == 2]
    if not p2:
        return H1Result(0, 0.0, 0.0, None, regime_changes_30d)

    regime = [r for r in p2 if r["exit_reason"] == "regime_flip"]
    r_values = [r.get("pnl_r") for r in regime if r.get("pnl_r") is not None]

    return H1Result(
        n_exits=len(p2),
        regime_flip_share=len(regime) / len(p2),
        reentry_rate=reentry_rate_after(rows, "regime_flip"),
        regime_flip_median_r=statistics.median(r_values) if r_values else None,
        regime_flips_30d=regime_changes_30d,
    )


def verdict_h1(res: H1Result) -> str:
    """Решающие правила в порядке, зафиксированном 08.08 до сбора данных."""
    if res.n_exits < MIN_SAMPLE:
        return (f"НЕДОСТАТОЧНО ДАННЫХ: {res.n_exits}/{MIN_SAMPLE} выходов "
                f"на policy_version=2 — ждём, ничего не решаем")

    if res.regime_flip_share < SHARE_THRESHOLD:
        return (f"НЕ ПОДТВЕРЖДЕНА: доля regime_flip "
                f"{res.regime_flip_share:.0%} < {SHARE_THRESHOLD:.0%} — "
                f"закрываем гипотезу")

    if res.regime_flips_30d < MIN_REGIME_FLIPS_30D:
        return (f"ВСПЛЕСК: смен режима за 30 дней {res.regime_flips_30d} — "
                f"период нерепрезентативен, продлеваем наблюдение")

    if res.regime_flip_median_r is not None and res.regime_flip_median_r >= 0:
        return (f"ВЫХОДЫ ЗАЩИТНЫЕ: медиана R по regime_flip "
                f"{res.regime_flip_median_r:+.3f} ≥ 0 — частота не дефект, "
                f"не трогаем")

    if res.reentry_rate < REENTRY_THRESHOLD:
        return (f"НЕ ПОДТВЕРЖДЕНА: возвратов внутри {REENTRY_WINDOW_H} ч "
                f"{res.reentry_rate:.0%} < {REENTRY_THRESHOLD:.0%} — выходы "
                f"не выглядят дребезгом")

    return (f"ПОДТВЕРЖДЕНА: доля regime_flip {res.regime_flip_share:.0%}, "
            f"возвратов {res.reentry_rate:.0%}, медиана R "
            f"{res.regime_flip_median_r:+.3f} — ставить подтверждение смены "
            f"режима (K=2 суточных снимка)")


# ---------------------------------------------------------------- гипотеза H2

MIN_SAMPLE_H2 = 40           # закрытых сделок с оценкой R
SIDE_GAP_THRESHOLD = 0.20    # насколько одна сторона должна отставать
MIN_PER_SIDE = 15


@dataclass(frozen=True)
class H2Result:
    n_closed: int
    avg_r: Optional[float]
    median_r: Optional[float]
    long_avg_r: Optional[float]
    short_avg_r: Optional[float]
    n_long: int
    n_short: int
    ci_low: Optional[float]
    ci_high: Optional[float]


def _bootstrap_ci(values: Sequence[float], iterations: int = 2000,
                  seed: int = 20260809) -> tuple[Optional[float], Optional[float]]:
    """95% доверительный интервал среднего. Сид фиксирован: отчёт,
    меняющийся от прогона к прогону, не годится для решения."""
    if len(values) < 2:
        return None, None
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(iterations):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[int(0.975 * len(means)) - 1]
    return lo, hi


def compute_h2(rows: Sequence[dict]) -> H2Result:
    closed = [r for r in rows
              if r.get("exit_reason") and r.get("pnl_r") is not None]
    if not closed:
        return H2Result(0, None, None, None, None, 0, 0, None, None)

    rs = [float(r["pnl_r"]) for r in closed]
    longs = [float(r["pnl_r"]) for r in closed
             if str(r.get("closed_direction", "")).upper() == "LONG"]
    shorts = [float(r["pnl_r"]) for r in closed
              if str(r.get("closed_direction", "")).upper() == "SHORT"]
    lo, hi = _bootstrap_ci(rs)

    return H2Result(
        n_closed=len(closed),
        avg_r=statistics.mean(rs),
        median_r=statistics.median(rs),
        long_avg_r=statistics.mean(longs) if longs else None,
        short_avg_r=statistics.mean(shorts) if shorts else None,
        n_long=len(longs), n_short=len(shorts),
        ci_low=lo, ci_high=hi,
    )


def verdict_h2(res: H2Result) -> str:
    """Правила в порядке, зафиксированном 09.08 до сбора данных."""
    if res.n_closed < MIN_SAMPLE_H2:
        return (f"НЕДОСТАТОЧНО ДАННЫХ: {res.n_closed}/{MIN_SAMPLE_H2} "
                f"закрытых с оценкой R")

    if (res.n_long >= MIN_PER_SIDE and res.n_short >= MIN_PER_SIDE
            and res.long_avg_r is not None and res.short_avg_r is not None
            and res.long_avg_r - res.short_avg_r >= SIDE_GAP_THRESHOLD):
        return (f"СТОРОНА: SHORT хуже LONG на "
                f"{res.long_avg_r - res.short_avg_r:+.2f}R — сузить тактику "
                f"до LONG, SHORT в наблюдение")

    if res.ci_high is not None and res.ci_high < 0 and (res.median_r or 0) < 0:
        return (f"СЛОЙ ТЕРЯЕТ: медиана {res.median_r:+.3f}, верхняя граница "
                f"интервала {res.ci_high:+.3f} < 0 — отключить эмиссию "
                f"тактических входов, выходы по политике оставить")

    if res.ci_low is not None and res.ci_low <= 0 <= (res.ci_high or 0):
        return (f"НЕ ДОКАЗАНО: интервал [{res.ci_low:+.3f}, "
                f"{res.ci_high:+.3f}] накрывает ноль — оставляем, "
                f"возвращаемся при n≥80")

    return (f"СЛОЙ ОКУПАЕТСЯ: avg R {res.avg_r:+.3f}, интервал "
            f"[{res.ci_low:+.3f}, {res.ci_high:+.3f}]")
