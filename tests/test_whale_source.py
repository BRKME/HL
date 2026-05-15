"""Tests for src/whale_source.py — pull HL leaderboard and pick candidates."""
from unittest.mock import patch, MagicMock

import pytest

from src.whale_source import (
    WhaleCandidate,
    fetch_leaderboard,
    parse_leaderboard_entry,
    pick_candidates,
    CandidateFilters,
)


# A trimmed but realistic leaderboard row shape.
# Real responses have a `leaderboardRows` key with one entry per trader.
SAMPLE_LEADERBOARD = {
    "leaderboardRows": [
        {
            "ethAddress": "0xaaa1111111111111111111111111111111111111",
            "accountValue": "5000000.0",
            "displayName": "BigWhale",
            "windowPerformances": [
                ["day",     {"pnl": "12000.0",   "roi": "0.0024", "vlm": "50000000"}],
                ["week",    {"pnl": "150000.0",  "roi": "0.030",  "vlm": "300000000"}],
                ["month",   {"pnl": "800000.0",  "roi": "0.16",   "vlm": "1200000000"}],
                ["allTime", {"pnl": "5500000.0", "roi": "1.10",   "vlm": "9000000000"}],
            ],
        },
        {
            "ethAddress": "0xbbb2222222222222222222222222222222222222",
            "accountValue": "30000.0",  # small account
            "displayName": "",
            "windowPerformances": [
                ["day",     {"pnl": "100.0",  "roi": "0.003",  "vlm": "100000"}],
                ["week",    {"pnl": "500.0",  "roi": "0.017",  "vlm": "500000"}],
                ["month",   {"pnl": "2000.0", "roi": "0.067",  "vlm": "2000000"}],
                ["allTime", {"pnl": "5000.0", "roi": "0.166",  "vlm": "5000000"}],
            ],
        },
        {
            "ethAddress": "0xccc3333333333333333333333333333333333333",
            "accountValue": "1500000.0",
            "displayName": "SteadyEddie",
            "windowPerformances": [
                ["day",     {"pnl": "-5000.0",   "roi": "-0.003", "vlm": "20000000"}],
                ["week",    {"pnl": "20000.0",   "roi": "0.013",  "vlm": "150000000"}],
                ["month",   {"pnl": "120000.0",  "roi": "0.080",  "vlm": "600000000"}],
                ["allTime", {"pnl": "900000.0",  "roi": "0.60",   "vlm": "4500000000"}],
            ],
        },
        {
            "ethAddress": "0xddd4444444444444444444444444444444444444",
            "accountValue": "2000000.0",
            "displayName": "LuckPump",
            "windowPerformances": [
                ["day",     {"pnl": "300000.0",  "roi": "0.15",  "vlm": "5000000"}],
                ["week",    {"pnl": "350000.0",  "roi": "0.17",  "vlm": "10000000"}],
                # month/allTime barely different from week — single spike
                ["month",   {"pnl": "360000.0",  "roi": "0.18",  "vlm": "15000000"}],
                ["allTime", {"pnl": "370000.0",  "roi": "0.18",  "vlm": "20000000"}],
            ],
        },
    ]
}


# ---------- parsing ----------

def test_parse_leaderboard_entry_extracts_window_performances():
    entry = SAMPLE_LEADERBOARD["leaderboardRows"][0]
    c = parse_leaderboard_entry(entry)
    assert c.address == "0xaaa1111111111111111111111111111111111111"
    assert c.account_value == pytest.approx(5_000_000)
    assert c.display_name == "BigWhale"
    assert c.pnl_day == pytest.approx(12_000)
    assert c.pnl_week == pytest.approx(150_000)
    assert c.pnl_month == pytest.approx(800_000)
    assert c.pnl_all_time == pytest.approx(5_500_000)
    assert c.vlm_month == pytest.approx(1_200_000_000)
    assert c.roi_month == pytest.approx(0.16)


def test_parse_leaderboard_entry_handles_missing_window():
    """Some accounts may not have a `week` row yet. Default to 0, no crash."""
    entry = {
        "ethAddress": "0xfff5555555555555555555555555555555555555",
        "accountValue": "100000",
        "windowPerformances": [
            ["day", {"pnl": "100", "roi": "0.001", "vlm": "1000000"}],
            # week / month / allTime missing
        ],
    }
    c = parse_leaderboard_entry(entry)
    assert c.pnl_day == pytest.approx(100)
    assert c.pnl_week == 0.0
    assert c.pnl_month == 0.0


def test_parse_leaderboard_entry_normalises_address_to_lowercase():
    entry = {
        "ethAddress": "0xAAA1111111111111111111111111111111111111",
        "accountValue": "100000",
        "windowPerformances": [],
    }
    c = parse_leaderboard_entry(entry)
    assert c.address == c.address.lower()


def test_parse_leaderboard_entry_handles_malformed_numbers():
    """Non-numeric strings should default to 0, not raise."""
    entry = {
        "ethAddress": "0xeee5555555555555555555555555555555555555",
        "accountValue": "not-a-number",
        "windowPerformances": [
            ["day", {"pnl": "garbage", "roi": "x", "vlm": "?"}],
        ],
    }
    c = parse_leaderboard_entry(entry)
    assert c.account_value == 0.0
    assert c.pnl_day == 0.0


# ---------- fetch ----------

def test_fetch_leaderboard_uses_stats_data_url():
    with patch("src.whale_source.requests.get") as mock_get:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = SAMPLE_LEADERBOARD
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp
        rows = fetch_leaderboard()
        assert mock_get.call_args.args[0] == \
            "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
        assert len(rows) == 4


def test_fetch_leaderboard_returns_empty_on_unexpected_format():
    with patch("src.whale_source.requests.get") as mock_get:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"unexpected": "shape"}
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp
        rows = fetch_leaderboard()
        assert rows == []


def test_fetch_leaderboard_raises_on_http_error():
    from src.whale_source import WhaleSourceError
    with patch("src.whale_source.requests.get") as mock_get:
        mock_get.side_effect = Exception("connection reset")
        with pytest.raises(WhaleSourceError):
            fetch_leaderboard()


# ---------- candidate filtering ----------

def _all_candidates() -> list[WhaleCandidate]:
    return [parse_leaderboard_entry(e) for e in SAMPLE_LEADERBOARD["leaderboardRows"]]


def test_pick_candidates_filters_small_accounts():
    """Default min_account_value=$100k drops the $30k account."""
    cands = pick_candidates(_all_candidates(), CandidateFilters())
    addrs = {c.address for c in cands}
    assert "0xbbb2222222222222222222222222222222222222" not in addrs


def test_pick_candidates_filters_negative_30d_pnl():
    """Default requires month PnL > 0 — but we keep negative-day, positive-month."""
    cands = pick_candidates(_all_candidates(), CandidateFilters())
    addrs = {c.address for c in cands}
    # SteadyEddie has -day but +month: should be kept
    assert "0xccc3333333333333333333333333333333333333" in addrs


def test_pick_candidates_filters_single_spike():
    """A trader whose month PnL ≈ week PnL ≈ allTime PnL = one lucky trade.
    We want sustained earners. Filter when allTime is barely above month."""
    cands = pick_candidates(_all_candidates(), CandidateFilters(spike_ratio_min=1.5))
    addrs = {c.address for c in cands}
    assert "0xddd4444444444444444444444444444444444444" not in addrs


def test_pick_candidates_sorts_by_month_pnl_desc():
    cands = pick_candidates(_all_candidates(), CandidateFilters())
    # BigWhale month=800k, SteadyEddie month=120k
    assert cands[0].address.startswith("0xaaa")
    assert cands[1].address.startswith("0xccc")


def test_pick_candidates_respects_top_n():
    cands = pick_candidates(_all_candidates(), CandidateFilters(top_n=1))
    assert len(cands) == 1


def test_pick_candidates_filters_zero_volume_month():
    """Whale must have actually traded in the month (min vlm)."""
    entries = _all_candidates() + [
        WhaleCandidate(
            address="0xfff0000000000000000000000000000000000000",
            display_name="Idle",
            account_value=1_000_000,
            pnl_day=0, pnl_week=0, pnl_month=200_000, pnl_all_time=200_000,
            vlm_day=0, vlm_week=0, vlm_month=0, vlm_all_time=0,
            roi_day=0, roi_week=0, roi_month=0.20, roi_all_time=0.20,
        )
    ]
    cands = pick_candidates(entries, CandidateFilters(min_vlm_month_usd=10_000_000))
    assert all(c.vlm_month >= 10_000_000 for c in cands)


def test_candidate_filters_defaults_are_reasonable():
    """Sanity check the defaults — they should not be empty/None where we use them."""
    f = CandidateFilters()
    assert f.min_account_value > 0
    assert f.min_pnl_month > 0
    assert f.min_vlm_month_usd > 0
    assert f.top_n > 0
    assert f.spike_ratio_min >= 1.0
