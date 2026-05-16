"""Tests for src/sl_visibility.py — detect SL orders from HL frontendOpenOrders."""
from unittest.mock import MagicMock, patch

import pytest

from src.sl_visibility import (
    SLOrder,
    is_stop_loss_order,
    parse_sl_order,
    find_sl_for_position,
    fetch_sl_orders_for_wallets,
)
from src.portfolio import AggregatedPerpPosition


# ---------- is_stop_loss_order: classification ----------

def test_classifies_stop_market_as_sl():
    raw = {
        "coin": "ETH", "side": "A", "limitPx": "0.0", "sz": "0.5",
        "oid": 1, "timestamp": 1, "triggerCondition": "Price below 2100.0",
        "isTrigger": True, "triggerPx": "2100.0", "orderType": "Stop Market",
        "reduceOnly": True, "isPositionTpsl": False, "origSz": "0.5",
    }
    assert is_stop_loss_order(raw) is True


def test_classifies_stop_limit_as_sl():
    raw = {
        "coin": "BTC", "side": "A", "limitPx": "60000", "sz": "0.1",
        "oid": 2, "triggerCondition": "Price below 61000.0",
        "isTrigger": True, "triggerPx": "61000.0", "orderType": "Stop Limit",
        "reduceOnly": True,
    }
    assert is_stop_loss_order(raw) is True


def test_classifies_trigger_market_as_sl():
    """HL UI TP/SL dialog produces orderType='Trigger Market'."""
    raw = {
        "coin": "ETH", "side": "A", "isTrigger": True,
        "triggerCondition": "Price below 2100.0", "triggerPx": "2100.0",
        "orderType": "Trigger Market", "sz": "0.5",
    }
    assert is_stop_loss_order(raw) is True


def test_classifies_sl_without_reduce_only_flag():
    """Phase 3.0.x: relaxed — many HL SL orders don't have reduceOnly=True."""
    raw = {
        "coin": "ETH", "side": "A", "isTrigger": True,
        "triggerCondition": "Price below 2100.0", "triggerPx": "2100.0",
        "orderType": "Stop Market", "reduceOnly": False, "sz": "0.5",
    }
    assert is_stop_loss_order(raw) is True


def test_classifies_position_tpsl_attached_sl():
    """isPositionTpsl=True marks position-attached TP/SL."""
    raw = {
        "coin": "ETH", "side": "A", "isTrigger": True,
        "triggerCondition": "Price below 2100.0", "triggerPx": "2100.0",
        "orderType": "", "isPositionTpsl": True, "sz": "0.5",
    }
    assert is_stop_loss_order(raw) is True


def test_rejects_take_profit_order():
    """Take profit on a long: side='A', orderType='Take Profit Market', trigger ABOVE entry."""
    raw = {
        "coin": "ETH", "side": "A", "orderType": "Take Profit Market",
        "triggerCondition": "Price above 2500.0", "isTrigger": True,
        "triggerPx": "2500.0", "reduceOnly": True,
    }
    assert is_stop_loss_order(raw) is False


def test_rejects_non_trigger_order():
    """Plain limit order, not a trigger."""
    raw = {
        "coin": "ETH", "side": "A", "orderType": "Limit",
        "triggerCondition": "N/A", "isTrigger": False,
        "triggerPx": "0.0", "reduceOnly": False,
    }
    assert is_stop_loss_order(raw) is False


def test_accepts_via_trigger_condition_text():
    """Even if orderType is empty but it's a trigger with 'Price below' condition."""
    raw = {
        "coin": "ETH", "side": "A", "orderType": "",
        "triggerCondition": "Price below 2100.0", "isTrigger": True,
        "triggerPx": "2100.0",
    }
    assert is_stop_loss_order(raw) is True


# ---------- parse_sl_order ----------

def test_parse_sl_order_extracts_fields():
    raw = {
        "coin": "ETH", "side": "A", "limitPx": "2090", "sz": "0.5",
        "oid": 12345, "timestamp": 1700000000000,
        "triggerCondition": "Price below 2100.0",
        "isTrigger": True, "triggerPx": "2100.0",
        "orderType": "Stop Limit", "reduceOnly": True,
    }
    sl = parse_sl_order(raw, account="main")
    assert sl is not None
    assert sl.coin == "ETH"
    assert sl.trigger_px == pytest.approx(2100.0)
    assert sl.size == pytest.approx(0.5)
    assert sl.account == "main"
    assert sl.protects_side == "long"  # side='A' means sell-to-close = protecting long


def test_parse_sl_for_short_position():
    """SL for short: side='B' (buy to close), triggerPx ABOVE entry."""
    raw = {
        "coin": "BTC", "side": "B", "limitPx": "66000", "sz": "0.1",
        "oid": 2, "triggerCondition": "Price above 65000.0",
        "isTrigger": True, "triggerPx": "65000.0", "orderType": "Stop Market",
        "reduceOnly": True,
    }
    sl = parse_sl_order(raw, account="main")
    assert sl.protects_side == "short"


def test_parse_sl_order_returns_none_for_non_sl():
    raw = {"coin": "ETH", "side": "A", "isTrigger": False, "reduceOnly": False}
    assert parse_sl_order(raw, account="main") is None


def test_parse_sl_handles_malformed_numbers():
    raw = {
        "coin": "ETH", "side": "A", "triggerPx": "not-a-number",
        "isTrigger": True, "reduceOnly": True, "orderType": "Stop Market",
        "sz": "0.5",
    }
    sl = parse_sl_order(raw, account="main")
    assert sl is None  # bad number → reject rather than emit garbage


def test_parse_rejects_take_profit_on_long():
    """side='A' (close long) + 'Price above' = TP, not SL."""
    raw = {
        "coin": "ETH", "side": "A", "isTrigger": True,
        "triggerCondition": "Price above 2500.0", "triggerPx": "2500.0",
        "orderType": "Trigger Market", "sz": "0.5",
    }
    assert parse_sl_order(raw, account="main") is None


def test_parse_rejects_take_profit_on_short():
    """side='B' (close short) + 'Price below' = TP, not SL."""
    raw = {
        "coin": "BTC", "side": "B", "isTrigger": True,
        "triggerCondition": "Price below 60000.0", "triggerPx": "60000.0",
        "orderType": "Trigger Market", "sz": "0.1",
    }
    assert parse_sl_order(raw, account="main") is None


def test_parse_accepts_long_sl_with_below_trigger():
    """The expected combination for long SL."""
    raw = {
        "coin": "ETH", "side": "A", "isTrigger": True,
        "triggerCondition": "Price below 2100.0", "triggerPx": "2100.0",
        "orderType": "Trigger Market", "sz": "0.5",
    }
    sl = parse_sl_order(raw, account="main")
    assert sl is not None and sl.protects_side == "long"
    assert sl.trigger_px == 2100.0


# ---------- children scanning ----------

def test_fetch_picks_up_attached_sl_in_children(monkeypatch):
    """HL nests attached SL in 'children' of a parent order."""
    from unittest.mock import MagicMock
    client = MagicMock()
    # parent is a non-trigger limit; child is the actual SL
    client.get_frontend_open_orders.return_value = [
        {
            "coin": "ETH", "side": "B", "isTrigger": False, "orderType": "Limit",
            "limitPx": "2200.0", "sz": "0.5", "oid": 100,
            "children": [
                {
                    "coin": "ETH", "side": "A", "isTrigger": True,
                    "orderType": "Trigger Market",
                    "triggerCondition": "Price below 2100.0",
                    "triggerPx": "2100.0", "sz": "0.5", "oid": 101,
                    "isPositionTpsl": True,
                },
            ],
        },
    ]
    result = fetch_sl_orders_for_wallets(client, [{"address": "0xaaa", "label": "main"}])
    assert len(result) == 1
    assert result[0].trigger_px == 2100.0
    assert result[0].oid == 101


# ---------- find_sl_for_position ----------

def _pos(coin="ETH", net_size=0.7, entry=2173.0):
    return AggregatedPerpPosition(
        coin=coin, net_size=net_size, weighted_entry=entry, total_pnl=0,
        contributors=[("main", net_size)], avg_leverage=10.0,
        max_liquidation_distance_pct=80.0,
    )


def _sl(coin="ETH", trigger=2100.0, side="long", size=0.7):
    return SLOrder(
        coin=coin, trigger_px=trigger, size=size, protects_side=side,
        order_type="Stop Market", oid=1, account="main",
    )


def test_find_sl_matches_coin_and_side_for_long():
    pos = _pos(coin="ETH", net_size=0.7)
    orders = [_sl(coin="ETH", trigger=2100.0, side="long")]
    found = find_sl_for_position(pos, orders)
    assert found is not None
    assert found.trigger_px == 2100.0


def test_find_sl_picks_tightest_for_long():
    """Multiple SLs on same coin/side: closest to mark = the protective one."""
    pos = _pos(coin="ETH", net_size=0.7, entry=2173.0)
    orders = [
        _sl(coin="ETH", trigger=2000.0, side="long"),  # far
        _sl(coin="ETH", trigger=2100.0, side="long"),  # tighter
        _sl(coin="ETH", trigger=2050.0, side="long"),
    ]
    found = find_sl_for_position(pos, orders)
    assert found.trigger_px == 2100.0  # the highest (closest below long entry)


def test_find_sl_picks_tightest_for_short():
    """For short, the tightest SL is the LOWEST triggerPx (closest above mark)."""
    pos = _pos(coin="BTC", net_size=-0.1)
    orders = [
        _sl(coin="BTC", trigger=70000.0, side="short"),
        _sl(coin="BTC", trigger=66000.0, side="short"),  # tighter
        _sl(coin="BTC", trigger=68000.0, side="short"),
    ]
    found = find_sl_for_position(pos, orders)
    assert found.trigger_px == 66000.0


def test_find_sl_skips_wrong_side():
    """SL for short shouldn't match a long position."""
    pos = _pos(coin="ETH", net_size=0.7)  # long
    orders = [_sl(coin="ETH", side="short")]
    assert find_sl_for_position(pos, orders) is None


def test_find_sl_returns_none_when_no_match():
    pos = _pos(coin="ETH")
    orders = [_sl(coin="BTC")]
    assert find_sl_for_position(pos, orders) is None


def test_find_sl_handles_empty_orders():
    pos = _pos()
    assert find_sl_for_position(pos, []) is None


# ---------- fetch_sl_orders_for_wallets ----------

def test_fetch_aggregates_sl_orders_across_wallets():
    """Each wallet returns its own open orders; combined list has all SLs."""
    client = MagicMock()
    def orders_for(addr):
        if addr == "0xaaa":
            return [
                {"coin": "ETH", "side": "A", "isTrigger": True, "reduceOnly": True,
                 "orderType": "Stop Market", "triggerPx": "2100.0", "sz": "0.5",
                 "oid": 1, "triggerCondition": "Price below 2100.0"},
            ]
        if addr == "0xbbb":
            return [
                {"coin": "BTC", "side": "A", "isTrigger": True, "reduceOnly": True,
                 "orderType": "Stop Limit", "triggerPx": "60000.0", "sz": "0.1",
                 "oid": 2, "triggerCondition": "Price below 60000.0", "limitPx": "59500"},
            ]
        return []
    client.get_frontend_open_orders = MagicMock(side_effect=orders_for)
    accounts = [
        {"address": "0xaaa", "label": "main"},
        {"address": "0xbbb", "label": "second"},
    ]
    result = fetch_sl_orders_for_wallets(client, accounts)
    assert len(result) == 2
    coins = {sl.coin for sl in result}
    assert coins == {"ETH", "BTC"}


def test_fetch_ignores_non_sl_orders_returned_by_hl():
    """Mix of SL + take-profit + plain limit — only SL survives."""
    client = MagicMock()
    client.get_frontend_open_orders.return_value = [
        # SL
        {"coin": "ETH", "side": "A", "isTrigger": True, "reduceOnly": True,
         "orderType": "Stop Market", "triggerPx": "2100.0", "sz": "0.5",
         "oid": 1, "triggerCondition": "Price below 2100.0"},
        # Take profit
        {"coin": "ETH", "side": "A", "isTrigger": True, "reduceOnly": True,
         "orderType": "Take Profit Market", "triggerPx": "2500.0", "sz": "0.5",
         "oid": 2, "triggerCondition": "Price above 2500.0"},
        # plain limit
        {"coin": "ETH", "side": "A", "isTrigger": False, "reduceOnly": False,
         "orderType": "Limit", "limitPx": "2200.0", "sz": "0.5", "oid": 3},
    ]
    result = fetch_sl_orders_for_wallets(client, [{"address": "0xaaa", "label": "main"}])
    assert len(result) == 1
    assert result[0].order_type == "Stop Market"


def test_fetch_survives_one_wallet_error():
    """If one wallet's getOrders fails, others still queried."""
    client = MagicMock()
    def side(addr):
        if addr == "0xfail":
            raise RuntimeError("HL hiccup")
        return [
            {"coin": "ETH", "side": "A", "isTrigger": True, "reduceOnly": True,
             "orderType": "Stop Market", "triggerPx": "2100.0", "sz": "0.5",
             "oid": 1, "triggerCondition": "Price below 2100.0"},
        ]
    client.get_frontend_open_orders.side_effect = side
    accounts = [
        {"address": "0xfail", "label": "main"},
        {"address": "0xok", "label": "second"},
    ]
    result = fetch_sl_orders_for_wallets(client, accounts)
    assert len(result) == 1  # only from 0xok


def test_fetch_empty_when_all_wallets_fail():
    client = MagicMock()
    client.get_frontend_open_orders.side_effect = RuntimeError("HL down")
    result = fetch_sl_orders_for_wallets(client, [
        {"address": "0xa", "label": "main"},
        {"address": "0xb", "label": "second"},
    ])
    assert result == []
