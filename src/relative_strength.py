"""Relative Strength vs BTC — observability metric for future evaluation.

Records each coin's price change vs BTC's price change over 30d and 90d
windows. NOT used in verdict computation (yet). After 3+ weeks of journal
data we'll check whether RS correlates with verdict correctness better
than the current factors (RSI/funding/swing). If yes → drop those, use
RS instead. If no → leave it as observability only.

Analyst feedback (June 9): 'для альткоинов HYPE/TAO/NEAR/ZEC очень
вероятно что Relative Strength окажется полезнее RSI и whale вместе
взятых, потому что альткоины живут за счёт относительной силы'.
"""
from __future__ import annotations

import logging
from typing import Optional


logger = logging.getLogger("relative_strength")


def compute_rs(
    coin_closes: list[float],
    btc_closes: list[float],
    lookback_days: int,
) -> Optional[float]:
    """Return Relative Strength = coin_return - btc_return over N days.

    Positive = coin outperformed BTC by N percentage points.
    Negative = coin underperformed BTC by N percentage points.
    None = insufficient data.

    Example: BTC +20%, TAO +80% over 30d → RS_30 = +60.
             BTC +20%, NEAR +5% over 30d → RS_30 = -15.
    """
    if not coin_closes or not btc_closes:
        return None
    if len(coin_closes) <= lookback_days or len(btc_closes) <= lookback_days:
        return None
    try:
        coin_now = float(coin_closes[-1])
        coin_then = float(coin_closes[-1 - lookback_days])
        btc_now = float(btc_closes[-1])
        btc_then = float(btc_closes[-1 - lookback_days])
        if coin_then <= 0 or btc_then <= 0:
            return None
        coin_return = (coin_now - coin_then) / coin_then * 100
        btc_return = (btc_now - btc_then) / btc_then * 100
        return coin_return - btc_return
    except (TypeError, ValueError, IndexError):
        return None


def compute_rs_pair(
    coin_closes: list[float],
    btc_closes: list[float],
) -> tuple[Optional[float], Optional[float]]:
    """Return (RS_30d, RS_90d). Either may be None if data is too short."""
    return (
        compute_rs(coin_closes, btc_closes, 30),
        compute_rs(coin_closes, btc_closes, 90),
    )
