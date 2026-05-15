"""Tests for src/portfolio.py — aggregation of perp/spot across N wallets."""
import pytest

from src.portfolio import (
    PerpPosition,
    SpotPosition,
    AggregatedPerpPosition,
    Portfolio,
    aggregate_perp,
    parse_perp_position,
    parse_spot_balance,
)


# ---------- parsing ----------

def test_parse_perp_position_long():
    raw = {
        "coin": "ETH",
        "szi": "0.0335",
        "entryPx": "2986.3",
        "leverage": {"type": "isolated", "value": 20, "rawUsd": "-95.059824"},
        "liquidationPx": "2866.26",
        "marginUsed": "4.97",
        "positionValue": "100.02",
        "unrealizedPnl": "-0.01",
        "returnOnEquity": "-0.003",
        "cumFunding": {"sinceOpen": "1.50", "sinceChange": "0.0", "allTime": "10.0"},
    }
    p = parse_perp_position(raw, account="main")
    assert p.coin == "ETH"
    assert p.size == pytest.approx(0.0335)
    assert p.side == "long"
    assert p.entry_price == pytest.approx(2986.3)
    assert p.leverage == 20
    assert p.leverage_type == "isolated"
    assert p.liquidation_price == pytest.approx(2866.26)
    assert p.unrealized_pnl == pytest.approx(-0.01)
    assert p.account == "main"
    # mark_price derived from positionValue/abs(szi)
    assert p.mark_price == pytest.approx(100.02 / 0.0335, rel=1e-3)
    assert p.funding_since_open == pytest.approx(1.50)


def test_parse_perp_position_short():
    raw = {
        "coin": "BTC",
        "szi": "-0.5",
        "entryPx": "65000",
        "leverage": {"type": "cross", "value": 5},
        "liquidationPx": "75000",
        "marginUsed": "650",
        "positionValue": "32500",
        "unrealizedPnl": "0.0",
        "returnOnEquity": "0.0",
        "cumFunding": {"sinceOpen": "0", "sinceChange": "0", "allTime": "0"},
    }
    p = parse_perp_position(raw, account="main")
    assert p.size == pytest.approx(-0.5)
    assert p.side == "short"
    assert p.leverage_type == "cross"


def test_parse_spot_balance_resolves_coin_name():
    """Spot 'coin' may be '@107' — must use resolver to get 'HYPE'."""
    resolver = lambda symbol: {"@107": "HYPE", "PURR/USDC": "PURR"}.get(symbol, symbol)
    raw = {"coin": "@107", "token": 150, "total": "42.5", "hold": "0.0", "entryNtl": "1500.0"}
    s = parse_spot_balance(raw, account="main", resolver=resolver)
    assert s.coin == "HYPE"
    assert s.total == pytest.approx(42.5)
    assert s.entry_notional == pytest.approx(1500.0)
    # average entry price = entryNtl / total
    assert s.avg_entry == pytest.approx(1500.0 / 42.5)


def test_parse_spot_skips_usdc():
    """USDC is a balance, not a position — should be filtered out by caller, but
    parser still produces a row. We test the caller's filter in Portfolio tests."""
    raw = {"coin": "USDC", "token": 0, "total": "1000.0", "hold": "0.0", "entryNtl": "0.0"}
    s = parse_spot_balance(raw, account="main", resolver=lambda x: x)
    assert s.coin == "USDC"
    # avg_entry undefined when entryNtl is 0
    assert s.avg_entry is None


# ---------- perp aggregation across wallets ----------

def _perp(coin, size, entry, account, pnl=0.0, lev=10, mark=None):
    """Test helper to build a PerpPosition with sensible defaults."""
    return PerpPosition(
        coin=coin,
        size=size,
        entry_price=entry,
        mark_price=mark if mark is not None else entry,
        unrealized_pnl=pnl,
        leverage=lev,
        leverage_type="cross",
        liquidation_price=entry * 0.5 if size > 0 else entry * 1.5,
        margin_used=abs(size) * entry / lev,
        position_value=abs(size) * (mark if mark is not None else entry),
        return_on_equity=0.0,
        funding_since_open=0.0,
        account=account,
    )


def test_aggregate_perp_single_wallet_passthrough():
    positions = [_perp("BTC", 0.5, 63000, "main")]
    agg = aggregate_perp(positions)
    assert len(agg) == 1
    a = agg[0]
    assert a.coin == "BTC"
    assert a.net_size == pytest.approx(0.5)
    assert a.weighted_entry == pytest.approx(63000)
    assert a.contributors == [("main", 0.5)]


def test_aggregate_perp_same_coin_two_wallets_same_side():
    """Two wallets long BTC → net long, weighted entry."""
    positions = [
        _perp("BTC", 0.5, 63000, "main", pnl=100),
        _perp("BTC", 1.0, 65000, "second", pnl=50),
    ]
    agg = aggregate_perp(positions)
    assert len(agg) == 1
    a = agg[0]
    assert a.net_size == pytest.approx(1.5)
    # weighted entry = (0.5*63000 + 1.0*65000) / 1.5
    assert a.weighted_entry == pytest.approx((0.5 * 63000 + 1.0 * 65000) / 1.5)
    assert a.total_pnl == pytest.approx(150)
    assert a.side == "long"
    assert set(a.contributors) == {("main", 0.5), ("second", 1.0)}


def test_aggregate_perp_long_and_short_net_out():
    """Wallet A long 1 BTC, wallet B short 0.3 BTC → net long 0.7."""
    positions = [
        _perp("BTC", 1.0, 63000, "main"),
        _perp("BTC", -0.3, 64000, "second"),
    ]
    agg = aggregate_perp(positions)
    a = agg[0]
    assert a.net_size == pytest.approx(0.7)
    assert a.side == "long"
    # weighted entry uses abs(size) — both contribute to entry
    expected_entry = (1.0 * 63000 + 0.3 * 64000) / 1.3
    assert a.weighted_entry == pytest.approx(expected_entry)


def test_aggregate_perp_long_and_short_equal_size_marks_flat():
    """Hedged: long 1 + short 1 → flat."""
    positions = [
        _perp("BTC", 1.0, 63000, "main"),
        _perp("BTC", -1.0, 64000, "second"),
    ]
    agg = aggregate_perp(positions)
    a = agg[0]
    assert a.net_size == pytest.approx(0.0)
    assert a.side == "flat"


def test_aggregate_perp_different_coins_kept_separate():
    positions = [
        _perp("BTC", 0.5, 63000, "main"),
        _perp("ETH", 5.0, 3200, "main"),
        _perp("ETH", 2.0, 3300, "second"),
    ]
    agg = aggregate_perp(positions)
    assert len(agg) == 2
    coins = {a.coin for a in agg}
    assert coins == {"BTC", "ETH"}
    eth = next(a for a in agg if a.coin == "ETH")
    assert eth.net_size == pytest.approx(7.0)


# ---------- dust filter ----------

def test_aggregate_perp_skips_dust():
    """Positions with notional < dust threshold are dropped."""
    positions = [
        _perp("BTC", 0.5, 63000, "main"),
        _perp("PEPE", 100, 0.05, "main"),  # notional = $5, below threshold
    ]
    agg = aggregate_perp(positions, dust_usd=10.0)
    coins = {a.coin for a in agg}
    assert coins == {"BTC"}


# ---------- Portfolio: full build from raw HL responses ----------

def test_portfolio_build_from_wallets_groups_correctly():
    """Smoke test: feed raw HL responses for 3 wallets, get aggregated portfolio."""
    raw_responses = {
        "main": {
            "perp": {
                "marginSummary": {"accountValue": "1000"},
                "assetPositions": [
                    {
                        "type": "oneWay",
                        "position": {
                            "coin": "BTC",
                            "szi": "0.5",
                            "entryPx": "63000",
                            "leverage": {"type": "cross", "value": 10},
                            "liquidationPx": "55000",
                            "marginUsed": "3150",
                            "positionValue": "31500",
                            "unrealizedPnl": "0.0",
                            "returnOnEquity": "0.0",
                            "cumFunding": {"sinceOpen": "0", "sinceChange": "0", "allTime": "0"},
                        },
                    }
                ],
            },
            "spot": {"balances": [
                {"coin": "USDC", "token": 0, "total": "500", "hold": "0", "entryNtl": "0"},
            ]},
        },
        "second": {
            "perp": {
                "marginSummary": {"accountValue": "500"},
                "assetPositions": [
                    {
                        "type": "oneWay",
                        "position": {
                            "coin": "BTC",
                            "szi": "0.2",
                            "entryPx": "64000",
                            "leverage": {"type": "cross", "value": 10},
                            "liquidationPx": "55000",
                            "marginUsed": "1280",
                            "positionValue": "12800",
                            "unrealizedPnl": "0.0",
                            "returnOnEquity": "0.0",
                            "cumFunding": {"sinceOpen": "0", "sinceChange": "0", "allTime": "0"},
                        },
                    }
                ],
            },
            "spot": {"balances": []},
        },
        "third": {
            "perp": {"marginSummary": {"accountValue": "100"}, "assetPositions": []},
            "spot": {"balances": [
                {"coin": "@107", "token": 150, "total": "10", "hold": "0", "entryNtl": "350"},
            ]},
        },
    }
    resolver = lambda s: {"@107": "HYPE"}.get(s, s)

    pf = Portfolio.from_raw(raw_responses, spot_resolver=resolver)

    # perp aggregated
    assert len(pf.perp) == 1
    btc = pf.perp[0]
    assert btc.coin == "BTC"
    assert btc.net_size == pytest.approx(0.7)

    # spot: USDC filtered, HYPE present
    assert len(pf.spot) == 1
    assert pf.spot[0].coin == "HYPE"
    assert pf.spot[0].total == pytest.approx(10)

    # total account value summed across wallets (1000 + 500 + 100)
    assert pf.total_account_value == pytest.approx(1600)


def test_portfolio_empty_input():
    pf = Portfolio.from_raw({}, spot_resolver=lambda s: s)
    assert pf.perp == []
    assert pf.spot == []
    assert pf.total_account_value == 0.0


def test_portfolio_spot_filters_usdc_and_dust():
    raw_responses = {
        "main": {
            "perp": {"marginSummary": {"accountValue": "100"}, "assetPositions": []},
            "spot": {"balances": [
                {"coin": "USDC", "token": 0, "total": "10000", "hold": "0", "entryNtl": "0"},
                {"coin": "PURR/USDC", "token": 1, "total": "1.0", "hold": "0", "entryNtl": "0.50"},  # dust
                {"coin": "@107", "token": 150, "total": "100", "hold": "0", "entryNtl": "3500"},
            ]},
        }
    }
    resolver = lambda s: {"@107": "HYPE", "PURR/USDC": "PURR"}.get(s, s)
    pf = Portfolio.from_raw(raw_responses, spot_resolver=resolver, spot_dust_usd=10.0)
    coins = {s.coin for s in pf.spot}
    assert coins == {"HYPE"}  # USDC and PURR (dust) filtered
