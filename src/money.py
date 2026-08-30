"""Единое форматирование денежных величин для всех сообщений.

Заведено 30.08.2026. Форматов было семнадцать штук по системе, каждый со
своим `:,.0f`, и утренняя правка в дайджесте создала ложное впечатление,
что цены вылечены: heartbeat в тот же день показал «вход $1 · SL $1 · TP
$1» по ASTER за $0.70.

Правило: значащая точность, а не фиксированная. Цена должна позволять
отличить вход от стопа — иначе строка бесполезна ровно для дешёвых монет,
которых в списке половина.
"""
from __future__ import annotations

from typing import Optional


def fmt_price(p: Optional[float]) -> str:
    """Цена с точностью, достаточной, чтобы различать уровни."""
    if p is None or p == 0:
        return "—"
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "—"

    if p >= 1000:
        return f"{round(p):,}".replace(",", " ")
    if p >= 100:
        out = f"{p:.1f}"
    elif p >= 1:
        out = f"{p:.4g}"
    elif p >= 0.01:
        out = f"{p:.4f}"
    else:
        out = f"{p:.8f}"
    return out.rstrip("0").rstrip(".") if "." in out else out
