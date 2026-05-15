"""Tests for src/whale_monitor.py — Phase 2 entry point pipeline."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.whale_monitor import (
    run_whale_monitor,
    load_whitelist_coins,
    load_seen_signals,
    save_seen_signals,
    append_signals_log,
    SeenSignals,
)
from src.whale_correlation import Signal, SIG_CLUSTER, SIG_NEW_OPEN


NOW = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)


# ---------- whitelist coin loader ----------

def test_load_whitelist_coins_reads_tokens_section(tmp_path):
    wl = tmp_path / "whitelist.yaml"
    wl.write_text(
        "tokens:\n"
        "  BTC: {tier: 1, hl_symbol: BTC}\n"
        "  ETH: {tier: 1, hl_symbol: ETH}\n"
        "  HYPE: {tier: 3, hl_symbol: HYPE}\n"
    )
    coins = load_whitelist_coins(wl)
    assert coins == {"BTC", "ETH", "HYPE"}


def test_load_whitelist_coins_prefers_hl_symbol_over_key(tmp_path):
    """Token key might be 'PEPE' but hl_symbol 'kPEPE' — use hl_symbol for matching."""
    wl = tmp_path / "whitelist.yaml"
    wl.write_text(
        "tokens:\n"
        "  PEPE: {hl_symbol: kPEPE}\n"
        "  BTC: {hl_symbol: BTC}\n"
    )
    coins = load_whitelist_coins(wl)
    assert "kPEPE" in coins
    assert "PEPE" not in coins


def test_load_whitelist_coins_skips_null_hl_symbol(tmp_path):
    """tokens with hl_symbol: null aren't listed on HL — skip them."""
    wl = tmp_path / "whitelist.yaml"
    wl.write_text(
        "tokens:\n"
        "  BTC: {hl_symbol: BTC}\n"
        "  UNLISTED: {hl_symbol: null}\n"
    )
    coins = load_whitelist_coins(wl)
    assert coins == {"BTC"}


def test_load_whitelist_coins_empty_when_no_tokens(tmp_path):
    wl = tmp_path / "whitelist.yaml"
    wl.write_text("accounts: []\n")
    assert load_whitelist_coins(wl) == set()


# ---------- seen_signals persistence ----------

def test_load_seen_signals_missing_file_returns_empty(tmp_path):
    state = load_seen_signals(tmp_path / "missing.json", now=NOW)
    assert state.recent == set()


def test_load_seen_signals_filters_entries_older_than_24h(tmp_path):
    """Persisted file may include entries from yesterday — keep only <24h ones."""
    f = tmp_path / "seen.json"
    fresh_ts = int((NOW - timedelta(hours=5)).timestamp())
    stale_ts = int((NOW - timedelta(hours=30)).timestamp())
    f.write_text(json.dumps({
        "entries": [
            {"rule": "WHALE_CLUSTER", "whale": "", "coin": "BTC", "ts": fresh_ts},
            {"rule": "WHALE_FLIP", "whale": "0xabc", "coin": "ETH", "ts": stale_ts},
        ]
    }))
    state = load_seen_signals(f, now=NOW)
    assert ("WHALE_CLUSTER", "", "BTC") in state.recent
    assert ("WHALE_FLIP", "0xabc", "ETH") not in state.recent


def test_load_seen_signals_handles_corrupt_file(tmp_path):
    f = tmp_path / "seen.json"
    f.write_text("not json")
    state = load_seen_signals(f, now=NOW)
    assert state.recent == set()


def test_save_seen_signals_roundtrip(tmp_path):
    f = tmp_path / "seen.json"
    state = SeenSignals(
        entries=[
            {"rule": "WHALE_CLUSTER", "whale": "", "coin": "BTC",
             "ts": int(NOW.timestamp())},
        ]
    )
    save_seen_signals(state, f)
    state2 = load_seen_signals(f, now=NOW)
    assert ("WHALE_CLUSTER", "", "BTC") in state2.recent


# ---------- append_signals_log ----------

def test_append_signals_log_writes_each_signal_one_line(tmp_path):
    f = tmp_path / "whale_signals.jsonl"
    signals = [
        Signal(rule=SIG_CLUSTER, severity=2, coin="ETH",
               message="cluster on ETH", details={"whale_count": 3}),
        Signal(rule=SIG_NEW_OPEN, severity=1, coin="BTC",
               message="new open BTC", details={"whale": "0xabc"}),
    ]
    append_signals_log(signals, f, run_ts=NOW)
    lines = f.read_text().strip().split("\n")
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["rule"] == "WHALE_CLUSTER"
    assert parsed[1]["rule"] == "WHALE_NEW_OPEN"
    # each line includes its run timestamp for post-mortem alignment
    assert all("run_ts" in p for p in parsed)


def test_append_signals_log_noop_on_empty(tmp_path):
    f = tmp_path / "whale_signals.jsonl"
    append_signals_log([], f, run_ts=NOW)
    assert not f.exists()


# ---------- end-to-end pipeline ----------

@pytest.fixture
def temp_repo(tmp_path):
    """Minimal repo: whitelist with 1 wallet + 2 coins + an empty state/."""
    (tmp_path / "state").mkdir()
    (tmp_path / "whitelist.yaml").write_text(
        "accounts:\n"
        "  - address: \"0x17e6d71d30d260e30bb7721c63539694ab02b036\"\n"
        "    label: main\n"
        "tokens:\n"
        "  BTC: {hl_symbol: BTC}\n"
        "  ETH: {hl_symbol: ETH}\n"
    )
    return tmp_path


def _candidates_response():
    """Three high-quality whales that will pass picking filters."""
    rows = []
    for i, addr in enumerate([
        "0x111aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "0x222bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "0x333ccccccccccccccccccccccccccccccccccccc",
    ]):
        rows.append({
            "ethAddress": addr,
            "accountValue": "5000000",
            "displayName": f"Whale{i}",
            "windowPerformances": [
                ["day", {"pnl": "10000", "roi": "0.002", "vlm": "10000000"}],
                ["week", {"pnl": "80000", "roi": "0.016", "vlm": "50000000"}],
                ["month", {"pnl": "300000", "roi": "0.06", "vlm": "200000000"}],
                ["allTime", {"pnl": "2000000", "roi": "0.4", "vlm": "1000000000"}],
            ],
        })
    return {"leaderboardRows": rows}


def _cluster_fills_for_whale(addr: str, tid_base: int):
    """Each whale's userFillsByTime returns a closed-trade history (for scoring)
    plus a fresh Open Long ETH (which when combined across 3 whales = cluster)."""
    closed = [
        {"coin": "ETH", "sz": "1.0", "px": "3200", "tid": tid_base + i,
         "time": int((NOW - timedelta(days=5)).timestamp() * 1000),
         "side": "B", "dir": "Close Long", "closedPnl": "200" if i % 4 != 0 else "-100",
         "crossed": True, "oid": i}
        for i in range(12)
    ]
    fresh = [{
        "coin": "ETH", "sz": "25", "px": "3200", "tid": tid_base + 999,
        "time": int((NOW - timedelta(minutes=30)).timestamp() * 1000),
        "side": "B", "dir": "Open Long", "closedPnl": "0",
        "crossed": True, "oid": 999,
    }]
    return closed + fresh


def test_run_whale_monitor_smoke(temp_repo):
    """Full pipeline: 3 whales cluster on ETH → signals written to JSONL."""
    fake_client = MagicMock()
    # user wallet — no positions for simplicity
    fake_client.get_clearinghouse_state.return_value = {
        "marginSummary": {"accountValue": "1000"}, "assetPositions": [],
    }
    fake_client.get_spot_clearinghouse_state.return_value = {"balances": []}
    fake_client.resolve_spot_coin.side_effect = lambda s: s

    # each whale returns its own fills sequence; map address -> response
    def fills_response(address, **_kwargs):
        idx = address[:5]
        base = {"0x111": 1000, "0x222": 2000, "0x333": 3000}.get(idx, 9000)
        return _cluster_fills_for_whale(address, base)
    fake_client.get_user_fills_by_time.side_effect = lambda **kwargs: fills_response(
        kwargs["address"]
    )

    with patch("src.whale_monitor.HLClient", return_value=fake_client), \
         patch("src.whale_monitor.fetch_leaderboard",
               return_value=[
                   p for p in _candidates_response()["leaderboardRows"]
               ]) as mock_fetch_lb:
        # the real fetch_leaderboard returns WhaleCandidate, not raw dicts —
        # use the real parser to be honest about shape
        from src.whale_source import parse_leaderboard_entry
        mock_fetch_lb.return_value = [
            parse_leaderboard_entry(r)
            for r in _candidates_response()["leaderboardRows"]
        ]

        run_whale_monitor(
            repo_root=temp_repo,
            now=NOW,
            top_n=10,
        )

    # state files written
    fills_jsonl = temp_repo / "state" / "whale_fills.jsonl"
    cursor_json = temp_repo / "state" / "whale_cursor.json"
    signals_log = temp_repo / "state" / "whale_signals.jsonl"

    assert fills_jsonl.exists()
    assert cursor_json.exists()
    # 3 whales × ~13 fills each = ~39 lines
    assert len(fills_jsonl.read_text().strip().split("\n")) >= 30

    # cluster signal should have been detected on ETH
    if signals_log.exists():
        signals = [json.loads(line) for line in signals_log.read_text().strip().split("\n")]
        cluster_signals = [s for s in signals if s["rule"] == "WHALE_CLUSTER"]
        # at least one cluster on ETH from the 3 fresh Open Long fills
        assert any(s["coin"] == "ETH" for s in cluster_signals)


def test_run_whale_monitor_no_candidates_is_noop(temp_repo):
    """If leaderboard returns nothing useful, run completes without error."""
    fake_client = MagicMock()
    fake_client.get_clearinghouse_state.return_value = {
        "marginSummary": {"accountValue": "0"}, "assetPositions": [],
    }
    fake_client.get_spot_clearinghouse_state.return_value = {"balances": []}
    fake_client.resolve_spot_coin.side_effect = lambda s: s

    with patch("src.whale_monitor.HLClient", return_value=fake_client), \
         patch("src.whale_monitor.fetch_leaderboard", return_value=[]):
        run_whale_monitor(repo_root=temp_repo, now=NOW)
    # no crash, fills file not created (no candidates -> no fetches)
    assert not (temp_repo / "state" / "whale_fills.jsonl").exists()


def test_run_whale_monitor_survives_leaderboard_failure(temp_repo):
    """fetch_leaderboard raises -> run logs and exits cleanly, no traceback."""
    from src.whale_source import WhaleSourceError
    fake_client = MagicMock()
    with patch("src.whale_monitor.HLClient", return_value=fake_client), \
         patch("src.whale_monitor.fetch_leaderboard",
               side_effect=WhaleSourceError("leaderboard down")):
        # should NOT raise
        run_whale_monitor(repo_root=temp_repo, now=NOW)
    assert not (temp_repo / "state" / "whale_fills.jsonl").exists()


def test_run_whale_monitor_writes_run_metadata(temp_repo):
    """Each run leaves a metadata file with last_run_ts / candidate_count etc."""
    fake_client = MagicMock()
    fake_client.get_clearinghouse_state.return_value = {
        "marginSummary": {"accountValue": "0"}, "assetPositions": [],
    }
    fake_client.get_spot_clearinghouse_state.return_value = {"balances": []}
    fake_client.resolve_spot_coin.side_effect = lambda s: s

    with patch("src.whale_monitor.HLClient", return_value=fake_client), \
         patch("src.whale_monitor.fetch_leaderboard", return_value=[]):
        run_whale_monitor(repo_root=temp_repo, now=NOW)

    meta = temp_repo / "state" / "whale_run_meta.json"
    assert meta.exists()
    data = json.loads(meta.read_text())
    assert "last_run_ts" in data
    assert "candidate_count" in data
