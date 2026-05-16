"""Tests for src/portfolio_performance.py — HL portfolio endpoint parsing.

Aggregates per-period PnL across 3 wallets and produces a small dataclass
for the daily report's '📈 Доходность' block.
"""
from unittest.mock import patch, MagicMock

import pytest

from src.portfolio_performance import (
    PerformanceSnapshot,
    PeriodStats,
    fetch_portfolio,
    parse_portfolio_response,
    fetch_combined_performance,
    PORTFOLIO_PERIODS,
)


# Realistic response shape from HL portfolio endpoint
SAMPLE_PORTFOLIO = [
    ["day", {
        "accountValueHistory": [
            [1747200000000, "1000.0"],
            [1747203600000, "1020.0"],
            [1747207200000, "1045.0"],
        ],
        "pnlHistory": [
            [1747200000000, "0.0"],
            [1747203600000, "20.0"],
            [1747207200000, "45.0"],
        ],
        "vlm": "5000.0",
    }],
    ["week", {
        "accountValueHistory": [
            [1746595200000, "900.0"],
            [1747207200000, "1045.0"],
        ],
        "pnlHistory": [
            [1746595200000, "0.0"],
            [1747207200000, "145.0"],
        ],
        "vlm": "32000.0",
    }],
    ["month", {
        "accountValueHistory": [
            [1744528800000, "800.0"],
            [1747207200000, "1045.0"],
        ],
        "pnlHistory": [
            [1744528800000, "0.0"],
            [1747207200000, "245.0"],
        ],
        "vlm": "180000.0",
    }],
    ["allTime", {
        "accountValueHistory": [
            [1700000000000, "500.0"],
            [1747207200000, "1045.0"],
        ],
        "pnlHistory": [
            [1700000000000, "0.0"],
            [1747207200000, "545.0"],
        ],
        "vlm": "1200000.0",
    }],
    # perpDay/perpWeek/etc. are also present but we don't need them in the
    # first iteration — combined (perp+spot) period is more meaningful.
    ["perpDay", {"accountValueHistory": [], "pnlHistory": [], "vlm": "0.0"}],
    ["perpWeek", {"accountValueHistory": [], "pnlHistory": [], "vlm": "0.0"}],
    ["perpMonth", {"accountValueHistory": [], "pnlHistory": [], "vlm": "0.0"}],
    ["perpAllTime", {"accountValueHistory": [], "pnlHistory": [], "vlm": "0.0"}],
]


# ---------- request shape ----------

def test_fetch_portfolio_uses_correct_endpoint():
    with patch("src.portfolio_performance._post") as mock_post:
        mock_post.return_value = SAMPLE_PORTFOLIO
        fetch_portfolio("0x17E6D71D30D260E30BB7721C63539694AB02B036")

        args, kwargs = mock_post.call_args
        payload = args[0] if args else kwargs.get("payload")
        assert payload == {
            "type": "portfolio",
            "user": "0x17e6d71d30d260e30bb7721c63539694ab02b036",
        }


def test_fetch_portfolio_returns_raw_list():
    with patch("src.portfolio_performance._post", return_value=SAMPLE_PORTFOLIO):
        result = fetch_portfolio("0x17e6d71d30d260e30bb7721c63539694ab02b036")
    assert isinstance(result, list)
    assert result[0][0] == "day"


# ---------- parsing one wallet's response ----------

def test_parse_portfolio_extracts_pnl_per_period():
    snap = parse_portfolio_response(SAMPLE_PORTFOLIO, address="0xabc")
    assert snap.address == "0xabc"
    assert snap.day.pnl == pytest.approx(45.0)
    assert snap.week.pnl == pytest.approx(145.0)
    assert snap.month.pnl == pytest.approx(245.0)
    assert snap.all_time.pnl == pytest.approx(545.0)


def test_parse_portfolio_extracts_current_account_value_from_day():
    """Latest accountValueHistory point in 'day' period is the most current value."""
    snap = parse_portfolio_response(SAMPLE_PORTFOLIO, address="0xabc")
    assert snap.current_account_value == pytest.approx(1045.0)


def test_parse_portfolio_computes_starting_value_per_period():
    """First accountValueHistory point of each period = its starting value."""
    snap = parse_portfolio_response(SAMPLE_PORTFOLIO, address="0xabc")
    assert snap.day.start_value == pytest.approx(1000.0)
    assert snap.week.start_value == pytest.approx(900.0)
    assert snap.month.start_value == pytest.approx(800.0)
    assert snap.all_time.start_value == pytest.approx(500.0)


def test_parse_portfolio_computes_roi_pct():
    """ROI% = (pnl / start_value) × 100."""
    snap = parse_portfolio_response(SAMPLE_PORTFOLIO, address="0xabc")
    assert snap.day.roi_pct == pytest.approx(4.5)
    assert snap.week.roi_pct == pytest.approx(145 / 900 * 100)
    assert snap.month.roi_pct == pytest.approx(245 / 800 * 100)


def test_parse_portfolio_extracts_vlm():
    snap = parse_portfolio_response(SAMPLE_PORTFOLIO, address="0xabc")
    assert snap.day.vlm == pytest.approx(5000.0)
    assert snap.all_time.vlm == pytest.approx(1_200_000.0)


def test_parse_portfolio_handles_empty_history():
    """A new wallet may have empty history for some periods."""
    response = [
        ["day", {"accountValueHistory": [], "pnlHistory": [], "vlm": "0.0"}],
        ["week", {"accountValueHistory": [], "pnlHistory": [], "vlm": "0.0"}],
        ["month", {"accountValueHistory": [], "pnlHistory": [], "vlm": "0.0"}],
        ["allTime", {"accountValueHistory": [], "pnlHistory": [], "vlm": "0.0"}],
    ]
    snap = parse_portfolio_response(response, address="0xabc")
    assert snap.day.pnl == 0.0
    assert snap.day.start_value == 0.0
    assert snap.day.roi_pct == 0.0


def test_parse_portfolio_handles_missing_periods():
    """If HL response is missing 'month' for some reason, default to zero."""
    response = [
        ["day", {"accountValueHistory": [[1, "100"]], "pnlHistory": [[1, "5"]], "vlm": "100"}],
        ["allTime", {"accountValueHistory": [[1, "100"]], "pnlHistory": [[1, "5"]], "vlm": "100"}],
        # week and month missing entirely
    ]
    snap = parse_portfolio_response(response, address="0xabc")
    assert snap.week.pnl == 0.0
    assert snap.month.pnl == 0.0
    assert snap.day.pnl == 5.0


def test_parse_portfolio_handles_garbage_numbers():
    """Defensive: malformed pnl/value strings shouldn't crash."""
    response = [
        ["day", {
            "accountValueHistory": [[1, "garbage"]],
            "pnlHistory": [[1, "also-garbage"]],
            "vlm": "x",
        }],
        ["week", {"accountValueHistory": [], "pnlHistory": [], "vlm": "0"}],
        ["month", {"accountValueHistory": [], "pnlHistory": [], "vlm": "0"}],
        ["allTime", {"accountValueHistory": [], "pnlHistory": [], "vlm": "0"}],
    ]
    snap = parse_portfolio_response(response, address="0xabc")
    assert snap.day.pnl == 0.0
    assert snap.day.start_value == 0.0


# ---------- combined fetch across 3 wallets ----------

def test_fetch_combined_aggregates_pnl_across_wallets():
    """Sum of PnL across wallets, weighted ROI by start_value."""
    addrs = ["0xaaa", "0xbbb", "0xccc"]

    def make_response(pnl_amount: float, start_value: float, current: float):
        ts = 1747200000000
        return [
            ["day", {
                "accountValueHistory": [[ts, str(start_value)], [ts + 1, str(current)]],
                "pnlHistory": [[ts, "0"], [ts + 1, str(pnl_amount)]],
                "vlm": "0",
            }],
            ["week", {
                "accountValueHistory": [[ts, str(start_value)], [ts + 1, str(current)]],
                "pnlHistory": [[ts, "0"], [ts + 1, str(pnl_amount * 3)]],
                "vlm": "0",
            }],
            ["month", {
                "accountValueHistory": [[ts, str(start_value)], [ts + 1, str(current)]],
                "pnlHistory": [[ts, "0"], [ts + 1, str(pnl_amount * 10)]],
                "vlm": "0",
            }],
            ["allTime", {
                "accountValueHistory": [[ts, str(start_value)], [ts + 1, str(current)]],
                "pnlHistory": [[ts, "0"], [ts + 1, str(pnl_amount * 30)]],
                "vlm": "0",
            }],
        ]

    responses = {
        addrs[0]: make_response(45, 1000, 1045),
        addrs[1]: make_response(20, 500, 520),
        addrs[2]: make_response(-10, 300, 290),
    }
    with patch("src.portfolio_performance._post",
               side_effect=lambda payload: responses[payload["user"]]):
        agg = fetch_combined_performance(addrs)

    assert agg.day.pnl == pytest.approx(45 + 20 - 10)
    assert agg.week.pnl == pytest.approx((45 + 20 - 10) * 3)
    assert agg.current_account_value == pytest.approx(1045 + 520 + 290)


def test_fetch_combined_skips_failed_wallets():
    """If one wallet's portfolio fetch fails, others still aggregated."""
    addrs = ["0xaaa", "0xbbb"]

    def mock_post(payload):
        if payload["user"] == "0xaaa":
            raise RuntimeError("HL hiccup")
        return SAMPLE_PORTFOLIO

    with patch("src.portfolio_performance._post", side_effect=mock_post):
        agg = fetch_combined_performance(addrs)

    # only second wallet contributed, but no crash
    assert agg.day.pnl == pytest.approx(45.0)  # from SAMPLE_PORTFOLIO
    assert agg.failed_wallets == ["0xaaa"]


def test_fetch_combined_handles_all_wallets_failing():
    """If every wallet fails, return an empty snapshot, not crash."""
    addrs = ["0xaaa", "0xbbb"]
    with patch("src.portfolio_performance._post", side_effect=RuntimeError("everything down")):
        agg = fetch_combined_performance(addrs)
    assert agg.day.pnl == 0.0
    assert agg.current_account_value == 0.0
    assert len(agg.failed_wallets) == 2


def test_fetch_combined_lowercase_addresses():
    """Mixed-case addresses must be normalised before the request."""
    called_with = []
    def mock_post(payload):
        called_with.append(payload["user"])
        return SAMPLE_PORTFOLIO

    with patch("src.portfolio_performance._post", side_effect=mock_post):
        fetch_combined_performance(["0xABCdef1234567890ABCDEF1234567890ABCDEF12"])
    assert called_with[0] == "0xabcdef1234567890abcdef1234567890abcdef12"


def test_periods_constant_covers_expected_set():
    """We should at minimum pull day/week/month/allTime."""
    assert "day" in PORTFOLIO_PERIODS
    assert "week" in PORTFOLIO_PERIODS
    assert "month" in PORTFOLIO_PERIODS
    assert "allTime" in PORTFOLIO_PERIODS
