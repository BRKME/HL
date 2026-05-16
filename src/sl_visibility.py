"""SL visibility — read user's stop-loss orders from HL.

The daily monitor knows about positions (clearinghouseState) but not orders.
This module bridges the gap: pulls frontendOpenOrders, filters to true SL
triggers, matches them to aggregated positions, and the renderer shows
'SL $X.XX (-Y.Y%)' next to each position instead of just 'до liq XX%'.

HL SL detection:
- isTrigger: True
- reduceOnly: True
- orderType contains 'Stop' OR triggerCondition contains 'Price below'/'Price above'
- Excludes Take Profit (orderType contains 'Take Profit')

Side mapping:
- side='A' (ask/sell): SL protects a LONG position (sell to close on price drop)
- side='B' (bid/buy):  SL protects a SHORT position (buy to close on price rise)

For multiple SLs on same coin/side, we surface the tightest (closest to mark)
since that's the one actually controlling risk.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from src.portfolio import AggregatedPerpPosition


logger = logging.getLogger("sl_visibility")


@dataclass(frozen=True)
class SLOrder:
    coin: str
    trigger_px: float
    size: float
    protects_side: str        # 'long' | 'short'
    order_type: str           # 'Stop Market' | 'Stop Limit' | etc
    oid: int
    account: str              # wallet label


# ----------------------------------------------------------- classification

def is_stop_loss_order(raw: dict) -> bool:
    """True if this open order is a stop-loss trigger (not a TP, not a plain limit)."""
    if not raw.get("isTrigger"):
        return False
    if not raw.get("reduceOnly"):
        return False

    order_type = str(raw.get("orderType", "") or "")
    if "Take Profit" in order_type:
        return False

    if "Stop" in order_type:
        return True

    # Fallback: some clients leave orderType blank but triggerCondition tells the story
    condition = str(raw.get("triggerCondition", "") or "")
    if "Price below" in condition or "Price above" in condition:
        return True

    return False


# ---------------------------------------------------------------- parsing

def _to_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_sl_order(raw: dict, account: str) -> Optional[SLOrder]:
    """Return an SLOrder if the raw order qualifies and parses cleanly, else None."""
    if not is_stop_loss_order(raw):
        return None

    trigger_px = _to_float(raw.get("triggerPx"))
    if trigger_px is None or trigger_px <= 0:
        return None

    size = _to_float(raw.get("sz") or raw.get("origSz"))
    if size is None or size <= 0:
        return None

    side = str(raw.get("side", "") or "")
    if side == "A":
        protects = "long"
    elif side == "B":
        protects = "short"
    else:
        return None

    return SLOrder(
        coin=str(raw.get("coin", "")),
        trigger_px=trigger_px,
        size=size,
        protects_side=protects,
        order_type=str(raw.get("orderType", "") or ""),
        oid=int(raw.get("oid", 0) or 0),
        account=account,
    )


# ------------------------------------------------------------ matching

def find_sl_for_position(
    pos: AggregatedPerpPosition,
    orders: list[SLOrder],
) -> Optional[SLOrder]:
    """Pick the tightest SL on the same coin/side as the position.

    'Tightest' = closest to current mark (the protective one if user has
    multiple SLs stacked, e.g. partial scale-outs).
    """
    if pos.side not in ("long", "short"):
        return None
    matching = [
        o for o in orders
        if o.coin == pos.coin and o.protects_side == pos.side
    ]
    if not matching:
        return None
    if pos.side == "long":
        # for long, SL is below price; tightest = highest triggerPx
        return max(matching, key=lambda o: o.trigger_px)
    else:
        # for short, SL is above price; tightest = lowest triggerPx
        return min(matching, key=lambda o: o.trigger_px)


# ------------------------------------------------------------ fetch

def fetch_sl_orders_for_wallets(
    client,
    accounts: list[dict],
) -> list[SLOrder]:
    """Aggregate SL orders across all wallets. Never raises — bad wallets logged."""
    out: list[SLOrder] = []
    for acc in accounts:
        addr = acc.get("address", "")
        label = acc.get("label") or addr[:10]
        try:
            raw_orders = client.get_frontend_open_orders(addr) or []
        except Exception as e:
            logger.warning("frontend_open_orders failed for %s: %s", addr[:10], e)
            continue
        if not isinstance(raw_orders, list):
            continue
        for raw in raw_orders:
            if not isinstance(raw, dict):
                continue
            sl = parse_sl_order(raw, account=label)
            if sl is not None:
                out.append(sl)
    return out
