"""Рекомендация плеча и размера — пре-регистрация 05.07.2026.

Запрос оператора: «система должна предлагать плечо». Квантовый принцип:
плечо — не цель, а СЛЕДСТВИЕ стопа и волатильности. Риск фиксирован
(1% депозита на сделку), из него и дистанции стопа выводится размер;
плечо капится сверху и никогда не растёт от волатильности — наоборот.

Политика (первое применимое сверху вниз):
  капы по классу:      BTC/ETH -> 3x, все альты -> 2x
  выравнивание:        вердикт совпал с режимом (LONG+BULL / SHORT+BEAR)
                       -> полный кап; TRANSITION/неизвестный -> кап-1;
                       против режима -> 1x (такие сигналы гейт и так режет,
                       но политика обязана быть тотальной)
  широкий стоп >=6%:   плечо // 2 (волатильный вход = меньше плеча)
  пол:                 1x
Сайзинг: size% = риск 1% / дистанция стопа, кап 50% депозита.
"""
from __future__ import annotations

from typing import Optional

TIER1 = {"BTC", "ETH"}
RISK_PCT = 1.0          # риск на сделку, % депозита
WIDE_STOP_PCT = 6.0     # порог «широкого» стопа
SIZE_CAP_PCT = 50.0     # максимум размера позиции, % депозита

_ALIGNED = {("LONG", "BULL"), ("SHORT", "BEAR")}
_COUNTER = {("LONG", "BEAR"), ("SHORT", "BULL")}


def suggest(coin: str, direction: str, regime: Optional[str],
            entry: float, sl: float) -> Optional[dict]:
    """Плечо и размер для сигнала. None при вырожденных входных."""
    try:
        entry, sl = float(entry), float(sl)
    except (TypeError, ValueError):
        return None
    if entry <= 0 or sl <= 0 or entry == sl:
        return None
    stop_dist_pct = abs(entry - sl) / entry * 100

    cap = 3 if coin in TIER1 else 2
    pair = (direction, regime)
    if pair in _ALIGNED:
        lev = cap
    elif pair in _COUNTER:
        lev = 1
    else:                       # TRANSITION / None / прочее
        lev = max(1, cap - 1)
    if stop_dist_pct >= WIDE_STOP_PCT:
        lev = max(1, lev // 2)

    size_pct = min(SIZE_CAP_PCT, RISK_PCT / stop_dist_pct * 100)
    return {
        "leverage": lev,
        "size_pct_equity": round(size_pct, 1),
        "stop_dist_pct": round(stop_dist_pct, 2),
        "note": (f"риск {RISK_PCT:.0f}% депозита при стопе "
                 f"{stop_dist_pct:.1f}%"),
    }


def format_line(s: dict) -> str:
    return (f"⚖️ Плечо: {s['leverage']}x · размер ~{s['size_pct_equity']:.0f}% "
            f"депозита ({s['note']})")
