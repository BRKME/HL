"""Tests for whale_stance — long/short aggregation per coin."""
import json
from datetime import datetime, timedelta, timezone

from src.whale_stance import (
    WhaleStance, compute_stance, format_stance_line,
)


NOW = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)


def _fill(coin, direction, notional, days_ago=1, whale="0xabc"):
    """Synthesise a single fill."""
    ts = NOW - timedelta(days=days_ago)
    return {
        "whale": whale, "coin": coin,
        "direction": direction,
        "notional_usd": notional,
        "size": notional / 100, "price": 100.0,
        "tid": int(ts.timestamp() * 1000),
        "time_ms": int(ts.timestamp() * 1000),
        "side": "A", "crossed": True, "oid": 1,
    }


def _write_fills(path, fills):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for f in fills:
            fh.write(json.dumps(f) + "\n")


# ---------- WhaleStance properties ----------

def test_stance_long_pct_dominant():
    s = WhaleStance(coin="BTC",
                    long_notional=80_000, short_notional=20_000,
                    long_count=2, short_count=1)
    assert s.long_pct == 80.0
    assert s.bias == "long"


def test_stance_short_bias():
    s = WhaleStance(coin="BTC",
                    long_notional=10_000, short_notional=90_000,
                    long_count=1, short_count=3)
    assert s.bias == "short"
    assert s.long_pct == 10.0


def test_stance_mix_no_bias():
    s = WhaleStance(coin="BTC",
                    long_notional=50_000, short_notional=50_000,
                    long_count=2, short_count=2)
    assert s.bias is None


def test_stance_too_few_events_no_bias():
    """<3 events total → bias suppressed (sample too small)."""
    s = WhaleStance(coin="BTC",
                    long_notional=100_000, short_notional=0,
                    long_count=2, short_count=0)
    assert s.bias is None


def test_stance_zero_activity():
    s = WhaleStance(coin="BTC", long_notional=0, short_notional=0,
                    long_count=0, short_count=0)
    assert s.bias is None
    assert s.long_pct == 0.0


# ---------- compute_stance ----------

def test_compute_stance_aggregates_open_long_short(tmp_path):
    state_dir = tmp_path / "state"
    fills = [
        _fill("BTC", "Open Long", 50_000),
        _fill("BTC", "Open Long", 30_000),
        _fill("BTC", "Open Short", 20_000),
        _fill("ETH", "Open Long", 100_000),
    ]
    _write_fills(state_dir / "whale_fills.jsonl", fills)
    stances = compute_stance(state_dir, coins=["BTC", "ETH"], now=NOW)
    assert stances["BTC"].long_notional == 80_000
    assert stances["BTC"].short_notional == 20_000
    assert stances["BTC"].long_count == 2
    assert stances["BTC"].short_count == 1
    assert stances["ETH"].long_notional == 100_000


def test_compute_stance_ignores_close_events(tmp_path):
    """Close Long / Close Short don't represent new whale bias."""
    state_dir = tmp_path / "state"
    fills = [
        _fill("BTC", "Open Long", 50_000),
        _fill("BTC", "Close Long", 50_000),     # ignored
        _fill("BTC", "Close Short", 30_000),    # ignored
    ]
    _write_fills(state_dir / "whale_fills.jsonl", fills)
    stances = compute_stance(state_dir, coins=["BTC"], now=NOW)
    assert stances["BTC"].long_notional == 50_000
    assert stances["BTC"].short_notional == 0


def test_compute_stance_respects_lookback(tmp_path):
    state_dir = tmp_path / "state"
    fills = [
        _fill("BTC", "Open Long", 50_000, days_ago=2),   # in
        _fill("BTC", "Open Long", 30_000, days_ago=10),  # out (7d default)
    ]
    _write_fills(state_dir / "whale_fills.jsonl", fills)
    stances = compute_stance(state_dir, coins=["BTC"], now=NOW)
    assert stances["BTC"].long_notional == 50_000


def test_compute_stance_min_notional_filter(tmp_path):
    """Sub-10k fills are noise — filtered out by default."""
    state_dir = tmp_path / "state"
    fills = [
        _fill("BTC", "Open Long", 50_000),  # in
        _fill("BTC", "Open Long", 500),     # out
        _fill("BTC", "Open Short", 100),    # out
    ]
    _write_fills(state_dir / "whale_fills.jsonl", fills)
    stances = compute_stance(state_dir, coins=["BTC"], now=NOW)
    assert stances["BTC"].long_notional == 50_000
    assert stances["BTC"].short_notional == 0


def test_compute_stance_missing_file_returns_zeros(tmp_path):
    state_dir = tmp_path / "state"
    stances = compute_stance(state_dir, coins=["BTC", "ETH"], now=NOW)
    assert stances["BTC"].long_notional == 0
    assert stances["ETH"].long_notional == 0
    assert stances["BTC"].bias is None


def test_compute_stance_unknown_coins_skipped(tmp_path):
    state_dir = tmp_path / "state"
    fills = [_fill("DOGE", "Open Long", 50_000)]  # not in requested list
    _write_fills(state_dir / "whale_fills.jsonl", fills)
    stances = compute_stance(state_dir, coins=["BTC"], now=NOW)
    # BTC entry exists, but empty
    assert stances["BTC"].long_notional == 0
    # DOGE not in result
    assert "DOGE" not in stances


# ---------- format_stance_line ----------

def test_format_line_marks_long_bias():
    stances = {
        "BTC": WhaleStance("BTC", 80_000, 20_000, 3, 1),
        "ETH": WhaleStance("ETH", 50_000, 50_000, 2, 2),
    }
    line = format_stance_line(stances, coins_order=["BTC", "ETH"])
    assert line is not None
    assert "BTC 80%↑" in line
    # ETH is exactly 50/50 → mix
    assert "ETH 50% mix" in line


def test_format_line_marks_short_bias():
    stances = {
        "TAO": WhaleStance("TAO", 10_000, 90_000, 1, 3),
    }
    line = format_stance_line(stances, coins_order=["TAO"])
    assert "TAO 90%↓" in line


def test_format_line_shows_dash_for_zero_activity():
    stances = {
        "BTC": WhaleStance("BTC", 100_000, 0, 3, 0),
        "HYPE": WhaleStance("HYPE", 0, 0, 0, 0),
    }
    line = format_stance_line(stances, coins_order=["BTC", "HYPE"])
    assert "HYPE —" in line
    assert "BTC" in line


def test_format_line_returns_none_when_no_activity_anywhere():
    """If no coin has activity, no point showing the line."""
    stances = {
        "BTC": WhaleStance("BTC", 0, 0, 0, 0),
        "ETH": WhaleStance("ETH", 0, 0, 0, 0),
    }
    line = format_stance_line(stances, coins_order=["BTC", "ETH"])
    assert line is None


def test_format_line_preserves_coin_order():
    stances = {c: WhaleStance(c, 100_000, 0, 3, 0)
               for c in ["HYPE", "BTC", "ETH", "NEAR", "ZEC", "TAO"]}
    line = format_stance_line(
        stances, coins_order=["HYPE", "BTC", "ETH", "NEAR", "ZEC", "TAO"]
    )
    # bytes-order check
    last = -1
    for c in ["HYPE", "BTC", "ETH", "NEAR", "ZEC", "TAO"]:
        idx = line.find(c)
        assert idx > last
        last = idx
