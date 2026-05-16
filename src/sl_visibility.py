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
    is_position_attached: bool = False  # isPositionTpsl: dynamic size = whole position


# ----------------------------------------------------------- classification

def is_stop_loss_order(raw: dict) -> bool:
    """True if this open order is a stop-loss trigger (not a TP, not a plain limit).

    HL exposes SL in several shapes:
    1. orderType='Stop Market'/'Stop Limit' with isTrigger=True and reduceOnly=True
       — the canonical form
    2. orderType='Trigger Market'/'Trigger Limit' with triggerCondition starting
       with 'Price below' (for long) or 'Price above' (for short) — used when
       user places SL through HL's UI TP/SL dialog. May or may not be reduceOnly.
    3. isPositionTpsl=true — HL's "attached SL/TP" feature. Such orders are
       implicitly position-protective and may have less metadata.

    We accept (1), (2), (3); we reject anything with 'Take Profit' or 'TP'
    in orderType, and anything that is not a trigger at all.
    """
    if not raw.get("isTrigger"):
        # Some attached TP/SL orders are listed in 'children' of a parent
        # order rather than top level; this function only classifies a single
        # row. The caller iterates and we re-call on children separately.
        return False

    order_type = str(raw.get("orderType", "") or "")
    if "Take Profit" in order_type or "TP" in order_type:
        return False

    condition = str(raw.get("triggerCondition", "") or "")
    if "Take Profit" in condition:
        return False

    # Stop-named or Trigger-named orderType counts
    if "Stop" in order_type or "Trigger" in order_type:
        return True

    # Position-attached TP/SL: HL marks these isPositionTpsl=True
    if raw.get("isPositionTpsl"):
        return True

    # Fallback by triggerCondition wording
    if "Price below" in condition or "Price above" in condition:
        return True

    return False


def iter_sl_candidates(raw: dict):
    """Yield this row and all its children — HL nests attached TP/SL in children."""
    yield raw
    for child in (raw.get("children") or []):
        if isinstance(child, dict):
            yield child


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

    is_position_attached = bool(raw.get("isPositionTpsl"))

    # Position-attached SLs are dynamic-sized: HL stores sz=0 because the
    # protected size = whatever the position is at the moment of trigger.
    # For ordinary SLs (manually placed via order entry), we still require
    # a positive size as a sanity check.
    raw_size = _to_float(raw.get("sz") or raw.get("origSz"))
    if raw_size is None:
        return None
    if raw_size <= 0 and not is_position_attached:
        return None
    size = max(raw_size, 0.0)  # normalise; 0.0 is fine for attached

    side = str(raw.get("side", "") or "")
    # Side semantics:
    #  - 'A' (ask/sell): closes a long. SL for long = trigger BELOW; TP for long = ABOVE
    #  - 'B' (bid/buy):  closes a short. SL for short = trigger ABOVE; TP for short = BELOW
    condition = str(raw.get("triggerCondition", "") or "")
    if side == "A":
        # For a long-closing order: it's an SL only if triggered on price DROP
        if "Price above" in condition:
            return None  # this is take-profit, not stop-loss
        protects = "long"
    elif side == "B":
        # For a short-closing order: SL when price rises
        if "Price below" in condition:
            return None  # take-profit for short
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
        is_position_attached=is_position_attached,
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
    """Aggregate SL orders across all wallets. Never raises — bad wallets logged.

    Scans children[] of each order to catch HL's 'attached TP/SL' pattern
    (parent limit/market with SL nested as child).

    If env DEBUG_SL=1 is set, dumps the first non-empty raw response to
    state/_sl_debug.json for one-shot inspection.
    """
    import json as _json
    import os as _os
    out: list[SLOrder] = []
    debug_dumped = False

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

        if _os.environ.get("DEBUG_SL") == "1" and raw_orders and not debug_dumped:
            try:
                with open("state/_sl_debug.json", "w", encoding="utf-8") as fh:
                    _json.dump({"address": addr, "orders": raw_orders}, fh, indent=2)
                debug_dumped = True
                logger.info("DEBUG_SL: dumped %d orders from %s to state/_sl_debug.json",
                            len(raw_orders), addr[:10])
            except Exception as e:
                logger.warning("DEBUG_SL dump failed: %s", e)

        for raw in raw_orders:
            if not isinstance(raw, dict):
                continue
            # Each top-level order plus its children — HL sometimes nests
            # attached SL inside the parent ('children' array).
            for candidate in iter_sl_candidates(raw):
                sl = parse_sl_order(candidate, account=label)
                if sl is not None:
                    out.append(sl)
    return out
