"""Tests for src/decisions_log.py and src/matcher.py.

decisions.jsonl is written by main.py after every weekly run; each line is one
JSON record with picks[] holding the actual trade recommendations.
"""
from datetime import datetime, timedelta, timezone
import json

import pytest

from src.decisions_log import Decision, load_decisions, parse_decision_row
from src.matcher import match_positions, MatchResult
from src.portfolio import AggregatedPerpPosition


NOW = datetime(2026, 5, 14, 9, 0, tzinfo=timezone.utc)
ROW_TEMPLATE = {
    "ts": "2026-05-09T07:54:54.746596+00:00",
    "signal": "MODERATE",
    "leverage": 1,
    "reasons": [],
    "oracai": {"regime": "BULL"},
    "picks": [
        {
            "symbol": "BTC",
            "hl_symbol": "BTC",
            "entry": 80188.0,
            "alloc_usd": 200.0,
            "alloc_pct": 100,
            "sl_price": 75201.6,
            "sl_pct": -6.22,
            "sl_method": "atr",
            "atr14": 1994.55,
            "score": 2.106,
            "rsi_d1": 62.7,
        }
    ],
    "skipped": [],
}


# ---------- decisions_log: parsing ----------

def test_parse_single_pick_row():
    decisions = parse_decision_row(ROW_TEMPLATE)
    assert len(decisions) == 1
    d = decisions[0]
    assert d.coin == "BTC"
    assert d.entry == pytest.approx(80188.0)
    assert d.alloc_usd == pytest.approx(200.0)
    assert d.sl_price == pytest.approx(75201.6)
    assert d.atr14 == pytest.approx(1994.55)
    assert d.signal == "MODERATE"
    assert d.side == "long"
    # expected_size = alloc_usd / entry
    assert d.expected_size == pytest.approx(200.0 / 80188.0)


def test_parse_two_picks_row_expands_to_two_decisions():
    """STRONG 60/40 yields a row with two picks; each becomes its own Decision."""
    row = {**ROW_TEMPLATE, "signal": "STRONG", "picks": [
        {"symbol": "BTC", "hl_symbol": "BTC", "entry": 80000, "alloc_usd": 120, "sl_price": 76000,
         "sl_pct": -5.0, "sl_method": "atr", "atr14": 2000.0},
        {"symbol": "ETH", "hl_symbol": "ETH", "entry": 3200, "alloc_usd": 80, "sl_price": 3000,
         "sl_pct": -6.25, "sl_method": "atr", "atr14": 80.0},
    ]}
    decisions = parse_decision_row(row)
    assert len(decisions) == 2
    assert {d.coin for d in decisions} == {"BTC", "ETH"}
    assert all(d.signal == "STRONG" for d in decisions)


def test_parse_skip_or_exit_row_yields_no_decisions():
    """SKIP/EXIT rows have empty picks — they shouldn't create trades to match."""
    row = {**ROW_TEMPLATE, "signal": "SKIP", "picks": []}
    assert parse_decision_row(row) == []
    row = {**ROW_TEMPLATE, "signal": "EXIT", "picks": []}
    assert parse_decision_row(row) == []


def test_parse_row_handles_missing_optional_fields():
    """sl_method, score etc. might be absent in some older rows — don't crash."""
    minimal = {
        "ts": "2026-05-09T07:54:54.746596+00:00",
        "signal": "MODERATE",
        "picks": [{
            "symbol": "BTC", "hl_symbol": "BTC", "entry": 80000, "alloc_usd": 200,
            "sl_price": 76000, "sl_pct": -5.0, "atr14": 2000.0,
        }],
    }
    decisions = parse_decision_row(minimal)
    assert len(decisions) == 1
    assert decisions[0].sl_method == "atr"  # default


def test_parse_row_uses_hl_symbol_when_present():
    """If hl_symbol differs from symbol, prefer hl_symbol for matching with HL data."""
    row = {**ROW_TEMPLATE, "picks": [{
        "symbol": "BTC.D", "hl_symbol": "BTC", "entry": 80000, "alloc_usd": 200,
        "sl_price": 76000, "sl_pct": -5.0, "atr14": 2000.0,
    }]}
    d = parse_decision_row(row)[0]
    assert d.coin == "BTC"  # hl_symbol wins


def test_parse_row_falls_back_to_symbol_without_hl_symbol():
    row = {**ROW_TEMPLATE, "picks": [{
        "symbol": "BTC", "entry": 80000, "alloc_usd": 200,
        "sl_price": 76000, "sl_pct": -5.0, "atr14": 2000.0,
    }]}
    assert parse_decision_row(row)[0].coin == "BTC"


# ---------- decisions_log: file loading ----------

def test_load_decisions_from_file(tmp_path):
    f = tmp_path / "decisions.jsonl"
    f.write_text("\n".join([
        json.dumps(ROW_TEMPLATE),
        json.dumps({**ROW_TEMPLATE, "signal": "EXIT", "picks": []}),
        json.dumps({**ROW_TEMPLATE, "picks": [{
            "symbol": "ETH", "hl_symbol": "ETH", "entry": 3200, "alloc_usd": 150,
            "sl_price": 3000, "sl_pct": -6.25, "atr14": 80.0,
        }]}),
    ]) + "\n")
    decisions = load_decisions(f)
    assert len(decisions) == 2  # EXIT skipped
    assert {d.coin for d in decisions} == {"BTC", "ETH"}


def test_load_decisions_handles_blank_lines_and_corrupt_rows(tmp_path):
    f = tmp_path / "decisions.jsonl"
    f.write_text("\n".join([
        json.dumps(ROW_TEMPLATE),
        "",                                  # blank
        "this is not json",                  # corrupt
        '{"ts": "bad", "picks":',            # incomplete
        json.dumps({**ROW_TEMPLATE, "picks": [{
            "symbol": "ETH", "hl_symbol": "ETH", "entry": 3200, "alloc_usd": 150,
            "sl_price": 3000, "sl_pct": -6.25, "atr14": 80.0,
        }]}),
    ]) + "\n")
    decisions = load_decisions(f)
    assert len(decisions) == 2  # corrupt rows skipped, good rows kept


def test_load_decisions_missing_file_returns_empty(tmp_path):
    """Don't crash if decisions.jsonl doesn't exist yet."""
    assert load_decisions(tmp_path / "nonexistent.jsonl") == []


def test_load_decisions_lookback_filters_old_rows(tmp_path):
    f = tmp_path / "decisions.jsonl"
    old_ts = (NOW - timedelta(days=30)).isoformat()
    recent_ts = (NOW - timedelta(days=5)).isoformat()
    f.write_text("\n".join([
        json.dumps({**ROW_TEMPLATE, "ts": old_ts}),
        json.dumps({**ROW_TEMPLATE, "ts": recent_ts, "picks": [{
            "symbol": "ETH", "hl_symbol": "ETH", "entry": 3200, "alloc_usd": 150,
            "sl_price": 3000, "sl_pct": -6.25, "atr14": 80.0,
        }]}),
    ]) + "\n")
    decisions = load_decisions(f, lookback_days=14, now=NOW)
    assert len(decisions) == 1
    assert decisions[0].coin == "ETH"


# ---------- matcher: helpers to build test objects ----------

def _agg(coin, net_size, entry, pnl=0.0, contributors=None):
    return AggregatedPerpPosition(
        coin=coin,
        net_size=net_size,
        weighted_entry=entry,
        total_pnl=pnl,
        contributors=contributors or [("main", net_size)],
        avg_leverage=10.0,
        max_liquidation_distance_pct=20.0,
    )


def _dec(coin, entry, alloc_usd, ts_days_ago=3, signal="MODERATE", sl_price=None, atr=None):
    return Decision(
        ts=NOW - timedelta(days=ts_days_ago),
        signal=signal,
        coin=coin,
        entry=entry,
        alloc_usd=alloc_usd,
        expected_size=alloc_usd / entry,
        sl_price=sl_price if sl_price is not None else entry * 0.94,
        sl_pct=-6.0,
        sl_method="atr",
        atr14=atr if atr is not None else entry * 0.025,
        side="long",
    )


# ---------- matcher: tracked matches ----------

def test_match_exact_entry_and_size():
    """Decision entry=80000 size=200/80000, position weighted_entry=80000 size=0.0025."""
    decisions = [_dec("BTC", 80000, 200)]
    positions = [_agg("BTC", 0.0025, 80000)]
    results = match_positions(positions, decisions, now=NOW)
    assert len(results) == 1
    assert results[0].status == "tracked"
    assert results[0].decision is not None
    assert results[0].decision.coin == "BTC"


def test_match_within_entry_tolerance():
    """Position entry $80,800 (+1%) vs decision $80,000 — within ±2% default."""
    decisions = [_dec("BTC", 80000, 200)]
    positions = [_agg("BTC", 0.00248, 80800)]  # size also slightly off
    results = match_positions(positions, decisions, now=NOW)
    assert results[0].status == "tracked"


def test_match_outside_entry_tolerance_is_orphan():
    """Position entry $84,000 (+5%) vs decision $80,000 — beyond 2%, treat as orphan."""
    decisions = [_dec("BTC", 80000, 200)]
    positions = [_agg("BTC", 0.00238, 84000)]
    results = match_positions(positions, decisions, now=NOW)
    assert results[0].status == "orphan"
    assert results[0].decision is None


def test_match_within_size_tolerance():
    """Position size 10% larger than expected — within default tolerance."""
    decisions = [_dec("BTC", 80000, 200)]
    # expected_size = 200/80000 = 0.0025; position is 0.00275 (+10%)
    positions = [_agg("BTC", 0.00275, 80000)]
    results = match_positions(positions, decisions, now=NOW)
    assert results[0].status == "tracked"


def test_match_outside_size_tolerance_is_orphan():
    """Position size 30% larger — beyond default 15%, orphan."""
    decisions = [_dec("BTC", 80000, 200)]
    positions = [_agg("BTC", 0.00325, 80000)]  # 30% larger
    results = match_positions(positions, decisions, now=NOW)
    assert results[0].status == "orphan"


def test_match_short_position_is_always_orphan():
    """Weekly bot doesn't short; any short position is a manual trade."""
    decisions = [_dec("BTC", 80000, 200)]
    positions = [_agg("BTC", -0.0025, 80000)]
    results = match_positions(positions, decisions, now=NOW)
    assert results[0].status == "orphan"


def test_match_picks_closest_when_multiple_candidates():
    """Two recent decisions for BTC; matcher picks the one with closest entry."""
    decisions = [
        _dec("BTC", 78000, 200, ts_days_ago=10),
        _dec("BTC", 80500, 200, ts_days_ago=3),  # closer
    ]
    positions = [_agg("BTC", 0.0025, 80300)]
    results = match_positions(positions, decisions, now=NOW)
    assert results[0].status == "tracked"
    assert results[0].decision.entry == pytest.approx(80500)


def test_match_aggregated_across_wallets():
    """Position aggregated from 3 wallets with weighted_entry ~$80,000 matches one decision."""
    decisions = [_dec("BTC", 80000, 600)]  # decision size 600/80000 = 0.0075
    pos = _agg(
        "BTC", 0.0075, 80100,
        contributors=[("main", 0.003), ("second", 0.003), ("third", 0.0015)]
    )
    results = match_positions([pos], decisions, now=NOW)
    assert results[0].status == "tracked"
    assert len(results[0].position.contributors) == 3


def test_match_days_in_position_calculated():
    """days_in_position is days between decision.ts and now."""
    decisions = [_dec("BTC", 80000, 200, ts_days_ago=5)]
    positions = [_agg("BTC", 0.0025, 80000)]
    results = match_positions(positions, decisions, now=NOW)
    assert results[0].days_in_position == 5


# ---------- matcher: orphan classification ----------

def test_position_with_no_matching_coin_is_orphan():
    decisions = [_dec("BTC", 80000, 200)]
    positions = [_agg("DOGE", 1000, 0.15)]
    results = match_positions(positions, decisions, now=NOW)
    assert results[0].status == "orphan"
    assert results[0].decision is None
    assert results[0].days_in_position is None


def test_no_decisions_yields_all_orphans():
    positions = [_agg("BTC", 0.5, 80000), _agg("ETH", 5, 3200)]
    results = match_positions(positions, [], now=NOW)
    assert len(results) == 2
    assert all(r.status == "orphan" for r in results)


def test_empty_portfolio_yields_no_results():
    decisions = [_dec("BTC", 80000, 200)]
    assert match_positions([], decisions, now=NOW) == []


# ---------- matcher: configurable tolerances ----------

def test_match_with_stricter_tolerance():
    """Override default tolerances via parameters."""
    decisions = [_dec("BTC", 80000, 200)]
    positions = [_agg("BTC", 0.0025, 80800)]  # 1% off
    # stricter 0.5% tolerance — should now be orphan
    results = match_positions(positions, decisions, now=NOW, entry_tolerance_pct=0.5)
    assert results[0].status == "orphan"
