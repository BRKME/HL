"""Tests for src/hl_api.py spot meta parsing."""
from unittest.mock import patch

from src.hl_api import fetch_spot_meta_and_ctxs


SPOT_META_RESPONSE = [
    {
        "tokens": [
            {"name": "USDC", "index": 0, "szDecimals": 8, "weiDecimals": 8},
            {"name": "PURR", "index": 1, "szDecimals": 1, "weiDecimals": 6},
            {"name": "HYPE", "index": 150, "szDecimals": 2, "weiDecimals": 8},
            {"name": "OMNIX", "index": 200, "szDecimals": 2, "weiDecimals": 6},
        ],
        "universe": [
            {"name": "PURR/USDC", "tokens": [1, 0], "index": 0, "isCanonical": True},
            {"name": "@107", "tokens": [150, 0], "index": 107, "isCanonical": False},
            {"name": "@201", "tokens": [200, 0], "index": 201, "isCanonical": False},
        ],
    },
    [
        {"coin": "PURR/USDC", "markPx": "0.14967", "midPx": "0.14966",
         "prevDayPx": "0.15719", "dayBaseVlm": "22964913.0"},
        {"coin": "@107", "markPx": "42.5", "midPx": "42.50",
         "prevDayPx": "41.0", "dayBaseVlm": "100000"},
        {"coin": "@201", "markPx": "0.05", "midPx": "0.0498",
         "prevDayPx": "0.048", "dayBaseVlm": "5000"},
    ],
]


def test_fetch_spot_meta_resolves_at_index_to_token_name():
    with patch("src.hl_api._post", return_value=SPOT_META_RESPONSE):
        result = fetch_spot_meta_and_ctxs()
    assert "HYPE" in result  # @107 resolved
    assert result["HYPE"]["mark"] == 42.5
    assert result["HYPE"]["prev_day"] == 41.0


def test_fetch_spot_meta_handles_purr_pair_name():
    with patch("src.hl_api._post", return_value=SPOT_META_RESPONSE):
        result = fetch_spot_meta_and_ctxs()
    assert "PURR" in result
    assert result["PURR"]["mark"] == 0.14967


def test_fetch_spot_meta_skips_unknown_pairs():
    """Pairs whose tokens aren't in metadata should be skipped, not crash."""
    bad_response = [
        {"tokens": [], "universe": [{"name": "@999", "tokens": [999, 0]}]},
        [{"coin": "@999", "markPx": "1.0", "prevDayPx": "0.9"}],
    ]
    with patch("src.hl_api._post", return_value=bad_response):
        result = fetch_spot_meta_and_ctxs()
    assert result == {}


def test_fetch_spot_meta_handles_missing_prev_day():
    """prevDayPx might be absent for new listings — should not crash."""
    response = [
        SPOT_META_RESPONSE[0],
        [{"coin": "@107", "markPx": "42.5", "midPx": "42.5"}],
    ]
    with patch("src.hl_api._post", return_value=response):
        result = fetch_spot_meta_and_ctxs()
    assert result["HYPE"]["prev_day"] is None
    assert result["HYPE"]["mark"] == 42.5
