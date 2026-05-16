"""Tests for src/whale_scoring.py — win-rate and PnL stats per whale."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.whale_scoring import (
    WhaleScore,
    score_from_fills,
    load_fills_for_whale,
    score_whale,
    INSUFFICIENT_DATA,
    MIN_CLOSED_TRADES,
)
from src.whale_tracker import WhaleFill


NOW = datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc)


def _fill(
    whale="0xabc", coin="BTC", closed_pnl=0.0, time_offset_days=1,
    notional=10_000.0, tid=1,
) -> WhaleFill:
    t = NOW - timedelta(days=time_offset_days)
    return WhaleFill(
        whale=whale, coin=coin, side="B", direction="Close Long",
        size=notional / 63000, price=63000.0, notional_usd=notional,
        tid=tid, time_ms=int(t.timestamp() * 1000),
        closed_pnl=closed_pnl, crossed=True, oid=tid,
    )


# ---------- score_from_fills basic stats ----------

def test_score_returns_insufficient_when_too_few_closed_trades():
    """Below MIN_CLOSED_TRADES (10) we don't trust the win-rate."""
    fills = [_fill(closed_pnl=100.0, tid=i) for i in range(5)]
    score = score_from_fills(fills, whale="0xabc", now=NOW)
    assert score.status == INSUFFICIENT_DATA
    assert score.closed_trades == 5


def test_score_ignores_open_trades_in_winrate():
    """Fills with closed_pnl == 0 are OPEN, not closed — exclude from winrate denominator."""
    fills = (
        [_fill(closed_pnl=100.0, tid=i) for i in range(8)]      # 8 wins
        + [_fill(closed_pnl=-50.0, tid=10 + i) for i in range(2)]  # 2 losses
        + [_fill(closed_pnl=0.0, tid=20 + i) for i in range(50)]   # 50 opens — ignored
    )
    score = score_from_fills(fills, whale="0xabc", now=NOW)
    assert score.status == "ok"
    assert score.closed_trades == 10
    assert score.win_rate == pytest.approx(0.8)


def test_score_calculates_avg_pnl():
    fills = [_fill(closed_pnl=100.0, tid=i) for i in range(8)] + \
            [_fill(closed_pnl=-200.0, tid=10 + i) for i in range(2)]
    # total = 800 - 400 = 400 over 10 trades -> avg 40
    score = score_from_fills(fills, whale="0xabc", now=NOW)
    assert score.total_pnl == pytest.approx(400.0)
    assert score.avg_pnl == pytest.approx(40.0)


def test_score_tracks_best_and_worst():
    fills = [_fill(closed_pnl=pnl, tid=i) for i, pnl in enumerate([
        100.0, 50.0, -30.0, 5000.0, -1200.0, 200.0, 75.0, -15.0, 300.0, 60.0
    ])]
    score = score_from_fills(fills, whale="0xabc", now=NOW)
    assert score.best_trade == pytest.approx(5000.0)
    assert score.worst_trade == pytest.approx(-1200.0)


def test_score_filters_to_30d_window():
    """Fills older than 30 days don't count."""
    recent = [_fill(closed_pnl=100.0, tid=i, time_offset_days=5) for i in range(10)]
    ancient = [_fill(closed_pnl=-9999.0, tid=100 + i, time_offset_days=60) for i in range(50)]
    score = score_from_fills(recent + ancient, whale="0xabc", now=NOW, window_days=30)
    assert score.closed_trades == 10
    assert score.win_rate == 1.0


def test_score_per_coin_breakdown():
    """Separate stats per coin — relevant when alerting on whitelist coin overlap."""
    fills = (
        [_fill(coin="BTC", closed_pnl=100.0, tid=i) for i in range(6)]
        + [_fill(coin="BTC", closed_pnl=-50.0, tid=10 + i) for i in range(2)]    # BTC: 6/8 = 0.75
        + [_fill(coin="ETH", closed_pnl=200.0, tid=20 + i) for i in range(4)]
        + [_fill(coin="ETH", closed_pnl=-300.0, tid=30 + i) for i in range(4)]   # ETH: 4/8 = 0.5
    )
    score = score_from_fills(fills, whale="0xabc", now=NOW)
    assert "BTC" in score.by_coin
    assert score.by_coin["BTC"].win_rate == pytest.approx(0.75)
    assert score.by_coin["ETH"].win_rate == pytest.approx(0.5)


def test_score_per_coin_skips_coins_with_fewer_than_5_trades():
    """Per-coin stats need at least 5 trades to be useful (lower than global 10)."""
    fills = (
        [_fill(coin="BTC", closed_pnl=100.0, tid=i) for i in range(10)]
        + [_fill(coin="DOGE", closed_pnl=100.0, tid=20 + i) for i in range(3)]  # too few
    )
    score = score_from_fills(fills, whale="0xabc", now=NOW)
    assert "BTC" in score.by_coin
    assert "DOGE" not in score.by_coin


def test_score_only_counts_target_whales_fills():
    """If JSONL has fills for many whales, score_from_fills filters by whale."""
    fills = (
        [_fill(whale="0xabc", closed_pnl=100.0, tid=i) for i in range(10)]
        + [_fill(whale="0xdef", closed_pnl=-9999.0, tid=20 + i) for i in range(50)]
    )
    score = score_from_fills(fills, whale="0xabc", now=NOW)
    assert score.closed_trades == 10
    assert score.win_rate == 1.0


# ---------- WhaleScore numeric edge cases ----------

def test_score_handles_all_losses():
    fills = [_fill(closed_pnl=-100.0, tid=i) for i in range(10)]
    score = score_from_fills(fills, whale="0xabc", now=NOW)
    assert score.win_rate == 0.0
    assert score.total_pnl == pytest.approx(-1000.0)


def test_score_handles_all_wins():
    fills = [_fill(closed_pnl=50.0, tid=i) for i in range(10)]
    score = score_from_fills(fills, whale="0xabc", now=NOW)
    assert score.win_rate == 1.0


# ---------- load_fills_for_whale: JSONL primary, 30d fallback ----------

def _write_jsonl(path: Path, fills: list[WhaleFill]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for f in fills:
            fh.write(f.to_json_line() + "\n")


def test_load_fills_uses_jsonl_when_enough_data(tmp_path):
    """When JSONL has >= MIN_CLOSED_TRADES closed trades, no API call needed."""
    fills = [_fill(whale="0xabc", closed_pnl=100.0, tid=i) for i in range(MIN_CLOSED_TRADES)]
    _write_jsonl(tmp_path / "whale_fills.jsonl", fills)
    client = MagicMock()

    result = load_fills_for_whale("0xabc", tmp_path, client, now=NOW)
    assert len(result) == MIN_CLOSED_TRADES
    client.get_user_fills_by_time.assert_not_called()


def test_load_fills_falls_back_to_api_when_jsonl_insufficient(tmp_path):
    """JSONL has only 3 closed trades — pull 30d from HL."""
    fills = [_fill(whale="0xabc", closed_pnl=100.0, tid=i) for i in range(3)]
    _write_jsonl(tmp_path / "whale_fills.jsonl", fills)

    client = MagicMock()
    # API returns 20 fresh fills, some open some closed
    api_response = (
        [{"coin": "BTC", "sz": "1.0", "px": "63000", "tid": 1000 + i, "time": int(NOW.timestamp() * 1000),
          "side": "B", "dir": "Close Long", "closedPnl": "200", "crossed": True, "oid": i}
         for i in range(15)]
        + [{"coin": "ETH", "sz": "2.0", "px": "3200", "tid": 2000 + i, "time": int(NOW.timestamp() * 1000),
            "side": "A", "dir": "Open Short", "closedPnl": "0", "crossed": True, "oid": i}
           for i in range(5)]
    )
    client.get_user_fills_by_time.return_value = api_response

    result = load_fills_for_whale("0xabc", tmp_path, client, now=NOW)
    assert len(result) >= 15  # at least the closed trades
    client.get_user_fills_by_time.assert_called_once()


def test_load_fills_handles_missing_jsonl(tmp_path):
    """No state file -> fallback to API directly."""
    client = MagicMock()
    client.get_user_fills_by_time.return_value = []
    result = load_fills_for_whale("0xabc", tmp_path, client, now=NOW)
    assert result == []
    client.get_user_fills_by_time.assert_called_once()


def test_load_fills_filters_by_whale_from_jsonl(tmp_path):
    """JSONL has mixed whales — only return fills for the target."""
    fills = (
        [_fill(whale="0xabc", closed_pnl=100.0, tid=i) for i in range(15)]
        + [_fill(whale="0xdef", closed_pnl=100.0, tid=100 + i) for i in range(50)]
    )
    _write_jsonl(tmp_path / "whale_fills.jsonl", fills)
    client = MagicMock()
    result = load_fills_for_whale("0xabc", tmp_path, client, now=NOW)
    assert all(f.whale == "0xabc" for f in result)
    assert len(result) == 15


def test_load_fills_normalises_address_case(tmp_path):
    fills = [_fill(whale="0xabc", closed_pnl=100.0, tid=i) for i in range(15)]
    _write_jsonl(tmp_path / "whale_fills.jsonl", fills)
    client = MagicMock()
    result = load_fills_for_whale("0xABC", tmp_path, client, now=NOW)  # mixed case
    assert len(result) == 15


# ---------- score_whale convenience wrapper ----------

def test_score_whale_full_pipeline(tmp_path):
    fills = (
        [_fill(whale="0xabc", coin="BTC", closed_pnl=100.0, tid=i) for i in range(8)]
        + [_fill(whale="0xabc", coin="BTC", closed_pnl=-50.0, tid=10 + i) for i in range(2)]
    )
    _write_jsonl(tmp_path / "whale_fills.jsonl", fills)
    client = MagicMock()
    score = score_whale("0xabc", tmp_path, client, now=NOW)
    assert score.status == "ok"
    assert score.win_rate == pytest.approx(0.8)
    assert score.closed_trades == 10
