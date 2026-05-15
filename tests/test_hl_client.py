"""Tests for src/hl_client.py — thin wrapper around Hyperliquid Info API.

We mock requests.post; the goal is to verify request shape and response parsing
against real response structures documented at
https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
"""
from unittest.mock import patch, MagicMock
import pytest

from src.hl_client import HLClient, HLAPIError


def _mock_post(payload, status=200):
    """Build a mock response object that mimics requests.Response."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return resp


# ---------- clearinghouseState ----------

CLEARINGHOUSE_SAMPLE = {
    "marginSummary": {
        "accountValue": "13109.482328",
        "totalMarginUsed": "4.967826",
        "totalNtlPos": "100.02765",
        "totalRawUsd": "13009.454678",
    },
    "crossMarginSummary": {
        "accountValue": "13104.514502",
        "totalMarginUsed": "0.0",
        "totalNtlPos": "0.0",
        "totalRawUsd": "13104.514502",
    },
    "crossMaintenanceMarginUsed": "0.0",
    "withdrawable": "13104.514502",
    "assetPositions": [
        {
            "type": "oneWay",
            "position": {
                "coin": "ETH",
                "szi": "0.0335",
                "entryPx": "2986.3",
                "leverage": {"type": "isolated", "value": 20, "rawUsd": "-95.059824"},
                "liquidationPx": "2866.26936529",
                "marginUsed": "4.967826",
                "maxLeverage": 50,
                "positionValue": "100.02765",
                "returnOnEquity": "-0.0026789",
                "unrealizedPnl": "-0.0134",
                "cumFunding": {
                    "allTime": "514.085417",
                    "sinceChange": "0.0",
                    "sinceOpen": "0.0",
                },
            },
        }
    ],
    "time": 1708622398623,
}


def test_clearinghouse_state_request_shape():
    """Client must POST {type, user} to the info endpoint."""
    with patch("src.hl_client.requests.post") as mock_post:
        mock_post.return_value = _mock_post(CLEARINGHOUSE_SAMPLE)
        c = HLClient()
        c.get_clearinghouse_state("0x17e6d71d30d260e30bb7721c63539694ab02b036")

        mock_post.assert_called_once()
        kwargs = mock_post.call_args.kwargs
        assert kwargs["json"] == {"type": "clearinghouseState", "user": "0x17e6d71d30d260e30bb7721c63539694ab02b036"}
        assert kwargs["timeout"] > 0


def test_clearinghouse_state_returns_parsed_response():
    with patch("src.hl_client.requests.post") as mock_post:
        mock_post.return_value = _mock_post(CLEARINGHOUSE_SAMPLE)
        c = HLClient()
        result = c.get_clearinghouse_state("0x17e6d71d30d260e30bb7721c63539694ab02b036")

        assert "assetPositions" in result
        assert len(result["assetPositions"]) == 1
        assert result["assetPositions"][0]["position"]["coin"] == "ETH"


def test_clearinghouse_state_handles_empty_positions():
    """Wallet with no perp activity returns assetPositions: []."""
    payload = {**CLEARINGHOUSE_SAMPLE, "assetPositions": []}
    with patch("src.hl_client.requests.post") as mock_post:
        mock_post.return_value = _mock_post(payload)
        result = HLClient().get_clearinghouse_state("0x17e6d71d30d260e30bb7721c63539694ab02b036")
        assert result["assetPositions"] == []


def test_address_is_lowercased():
    """HL pitfall: mixed-case addresses can fail or return empty; force lowercase."""
    with patch("src.hl_client.requests.post") as mock_post:
        mock_post.return_value = _mock_post(CLEARINGHOUSE_SAMPLE)
        c = HLClient()
        c.get_clearinghouse_state("0x17e6D71D30d260e30BB7721C63539694aB02b036")
        assert mock_post.call_args.kwargs["json"]["user"] == \
            "0x17e6d71d30d260e30bb7721c63539694ab02b036"


# ---------- spotClearinghouseState ----------

SPOT_SAMPLE = {
    "balances": [
        {"coin": "USDC", "token": 0, "total": "1234.56", "hold": "0.0", "entryNtl": "0.0"},
        {"coin": "@107", "token": 150, "total": "42.5", "hold": "0.0", "entryNtl": "1500.0"},
        {"coin": "PURR/USDC", "token": 1, "total": "100.0", "hold": "0.0", "entryNtl": "50.0"},
    ]
}


def test_spot_clearinghouse_state_request_shape():
    with patch("src.hl_client.requests.post") as mock_post:
        mock_post.return_value = _mock_post(SPOT_SAMPLE)
        HLClient().get_spot_clearinghouse_state("0x17e6d71d30d260e30bb7721c63539694ab02b036")
        assert mock_post.call_args.kwargs["json"] == {
            "type": "spotClearinghouseState",
            "user": "0x17e6d71d30d260e30bb7721c63539694ab02b036",
        }


def test_spot_balances_returned_as_is():
    with patch("src.hl_client.requests.post") as mock_post:
        mock_post.return_value = _mock_post(SPOT_SAMPLE)
        result = HLClient().get_spot_clearinghouse_state("0x17e6d71d30d260e30bb7721c63539694ab02b036")
        assert len(result["balances"]) == 3


# ---------- spotMeta (for @{index} -> token name mapping) ----------

SPOT_META_SAMPLE = {
    "tokens": [
        {"name": "USDC", "index": 0, "szDecimals": 8, "weiDecimals": 8},
        {"name": "PURR", "index": 1, "szDecimals": 0, "weiDecimals": 5},
        {"name": "HYPE", "index": 150, "szDecimals": 2, "weiDecimals": 8},
    ],
    "universe": [
        {"name": "PURR/USDC", "tokens": [1, 0], "index": 0, "isCanonical": True},
        {"name": "@107", "tokens": [150, 0], "index": 107, "isCanonical": False},
    ],
}


def test_spot_meta_request_shape():
    with patch("src.hl_client.requests.post") as mock_post:
        mock_post.return_value = _mock_post(SPOT_META_SAMPLE)
        HLClient().get_spot_meta()
        assert mock_post.call_args.kwargs["json"] == {"type": "spotMeta"}


def test_resolve_spot_coin_name_for_at_index():
    """@107 with tokens [150, 0] should resolve to 'HYPE' (token 150)."""
    with patch("src.hl_client.requests.post") as mock_post:
        mock_post.return_value = _mock_post(SPOT_META_SAMPLE)
        c = HLClient()
        name = c.resolve_spot_coin("@107")
        assert name == "HYPE"


def test_resolve_spot_coin_name_for_purr_format():
    """'PURR/USDC' is already a name — pass through."""
    with patch("src.hl_client.requests.post") as mock_post:
        mock_post.return_value = _mock_post(SPOT_META_SAMPLE)
        assert HLClient().resolve_spot_coin("PURR/USDC") == "PURR"


def test_resolve_spot_coin_unknown_returns_original():
    """Don't crash on unmapped indices — return the raw symbol."""
    with patch("src.hl_client.requests.post") as mock_post:
        mock_post.return_value = _mock_post(SPOT_META_SAMPLE)
        assert HLClient().resolve_spot_coin("@999") == "@999"


def test_spot_meta_is_cached():
    """Don't re-fetch meta on every coin resolution."""
    with patch("src.hl_client.requests.post") as mock_post:
        mock_post.return_value = _mock_post(SPOT_META_SAMPLE)
        c = HLClient()
        c.resolve_spot_coin("@107")
        c.resolve_spot_coin("PURR/USDC")
        c.resolve_spot_coin("@107")
        # only one spotMeta call regardless of resolutions
        meta_calls = [
            call for call in mock_post.call_args_list
            if call.kwargs["json"].get("type") == "spotMeta"
        ]
        assert len(meta_calls) == 1


# ---------- userFills ----------

FILLS_SAMPLE = [
    {
        "closedPnl": "0.0",
        "coin": "AVAX",
        "crossed": False,
        "dir": "Open Long",
        "hash": "0xa166e3fa63",
        "oid": 90542681,
        "px": "18.435",
        "side": "B",
        "startPosition": "26.86",
        "sz": "93.53",
        "time": 1681222254710,
        "fee": "0.01",
        "feeToken": "USDC",
        "tid": 118906512037719,
    }
]


def test_user_fills_request_shape():
    with patch("src.hl_client.requests.post") as mock_post:
        mock_post.return_value = _mock_post(FILLS_SAMPLE)
        HLClient().get_user_fills("0x17e6d71d30d260e30bb7721c63539694ab02b036")
        assert mock_post.call_args.kwargs["json"] == {
            "type": "userFills",
            "user": "0x17e6d71d30d260e30bb7721c63539694ab02b036",
        }


def test_user_fills_returns_list():
    with patch("src.hl_client.requests.post") as mock_post:
        mock_post.return_value = _mock_post(FILLS_SAMPLE)
        fills = HLClient().get_user_fills("0x17e6d71d30d260e30bb7721c63539694ab02b036")
        assert isinstance(fills, list) and len(fills) == 1


# ---------- error handling ----------

def test_http_error_raises_hl_api_error():
    with patch("src.hl_client.requests.post") as mock_post:
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "Internal error"
        resp.raise_for_status.side_effect = Exception("HTTP 500")
        mock_post.return_value = resp
        with pytest.raises(HLAPIError):
            HLClient().get_clearinghouse_state("0x17e6d71d30d260e30bb7721c63539694ab02b036")


def test_invalid_json_raises_hl_api_error():
    with patch("src.hl_client.requests.post") as mock_post:
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "not json"
        resp.json.side_effect = ValueError("invalid json")
        mock_post.return_value = resp
        with pytest.raises(HLAPIError):
            HLClient().get_clearinghouse_state("0x17e6d71d30d260e30bb7721c63539694ab02b036")
