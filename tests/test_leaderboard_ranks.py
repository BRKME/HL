"""Tests for src/leaderboard_ranks.py — track rank changes across runs."""
import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.leaderboard_ranks import (
    RankState,
    RankEntry,
    update_ranks_state,
    load_ranks_state,
    save_ranks_state,
    detect_rank_signals,
    append_history_snapshot,
    rotate_history_if_month_changed,
    cleanup_old_history_archives,
    NEW_ENTRANT_MIN_CONSECUTIVE,
    DROP_OFF_MIN_RUNS_IN_TOP,
)
from src.whale_source import WhaleCandidate


NOW = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)


def _candidate(address: str, pnl_month: float = 200_000, account_value: float = 1_000_000) -> WhaleCandidate:
    return WhaleCandidate(
        address=address.lower(),
        display_name="",
        account_value=account_value,
        pnl_day=1000, pnl_week=10000, pnl_month=pnl_month, pnl_all_time=pnl_month * 5,
        vlm_day=10000, vlm_week=70000, vlm_month=10_000_000, vlm_all_time=50_000_000,
        roi_day=0.001, roi_week=0.01, roi_month=0.05, roi_all_time=0.25,
    )


# ---------- RankState load/save ----------

def test_load_ranks_missing_file_returns_empty(tmp_path):
    state = load_ranks_state(tmp_path / "missing.json")
    assert state.entries == {}


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "ranks.json"
    state = RankState(entries={
        "0xabc": RankEntry(
            address="0xabc",
            first_seen_in_top_ms=int(NOW.timestamp() * 1000) - 100000,
            runs_in_top=5, consecutive_in_top=3, last_rank=12, last_seen_ms=int(NOW.timestamp() * 1000),
        ),
    })
    save_ranks_state(state, path)
    loaded = load_ranks_state(path)
    assert "0xabc" in loaded.entries
    assert loaded.entries["0xabc"].runs_in_top == 5


def test_load_ranks_handles_corrupt_file(tmp_path):
    path = tmp_path / "ranks.json"
    path.write_text("not json")
    state = load_ranks_state(path)
    assert state.entries == {}


# ---------- update_ranks_state ----------

def test_update_creates_new_entries_for_first_seen_addresses():
    state = RankState()
    candidates = [_candidate(f"0x{i:040x}") for i in range(5)]
    update_ranks_state(state, candidates, now=NOW)
    assert len(state.entries) == 5
    for c in candidates:
        e = state.entries[c.address]
        assert e.runs_in_top == 1
        assert e.consecutive_in_top == 1
        assert e.first_seen_in_top_ms == int(NOW.timestamp() * 1000)


def test_update_advances_consecutive_counter():
    state = RankState()
    candidates = [_candidate("0xabc")]
    update_ranks_state(state, candidates, now=NOW)
    update_ranks_state(state, candidates, now=NOW + timedelta(hours=4))
    update_ranks_state(state, candidates, now=NOW + timedelta(hours=8))
    e = state.entries["0xabc"]
    assert e.runs_in_top == 3
    assert e.consecutive_in_top == 3


def test_update_resets_consecutive_when_whale_drops_out_and_returns():
    state = RankState()
    update_ranks_state(state, [_candidate("0xabc")], now=NOW)
    update_ranks_state(state, [_candidate("0xabc")], now=NOW + timedelta(hours=4))
    # whale absent for one run
    update_ranks_state(state, [], now=NOW + timedelta(hours=8))
    # returns
    update_ranks_state(state, [_candidate("0xabc")], now=NOW + timedelta(hours=12))
    e = state.entries["0xabc"]
    assert e.runs_in_top == 3        # still counted across all runs
    assert e.consecutive_in_top == 1  # reset on return


def test_update_records_last_rank():
    """Rank = position in the input list (0-indexed -> rank 1)."""
    state = RankState()
    cands = [_candidate(f"0x{i:040x}", pnl_month=1_000_000 - i * 1000) for i in range(5)]
    update_ranks_state(state, cands, now=NOW)
    assert state.entries[cands[0].address].last_rank == 1
    assert state.entries[cands[4].address].last_rank == 5


def test_update_keeps_dropped_out_entries_for_history():
    """When a whale drops out, we keep their entry so DROP_OFF detection works.

    We don't delete; we just don't advance their counters and mark them
    as 'out of top' (consecutive_in_top = 0)."""
    state = RankState()
    update_ranks_state(state, [_candidate("0xabc")], now=NOW)
    update_ranks_state(state, [], now=NOW + timedelta(hours=4))
    assert "0xabc" in state.entries
    assert state.entries["0xabc"].consecutive_in_top == 0
    assert state.entries["0xabc"].runs_in_top == 1


# ---------- detect_rank_signals ----------

def test_new_entrant_signal_after_n_consecutive_runs():
    """NEW_ENTRANT fires after NEW_ENTRANT_MIN_CONSECUTIVE consecutive runs."""
    state = RankState()
    cands = [_candidate("0xnew")]
    for i in range(NEW_ENTRANT_MIN_CONSECUTIVE):
        update_ranks_state(state, cands, now=NOW + timedelta(hours=4 * i))
    signals = detect_rank_signals(state, prev_addresses=set(), current_addresses={"0xnew"}, now=NOW)
    new = [s for s in signals if s["rule"] == "WHALE_NEW_ENTRANT"]
    assert len(new) == 1
    assert new[0]["address"] == "0xnew"


def test_new_entrant_no_signal_before_threshold():
    """Single appearance — silent."""
    state = RankState()
    update_ranks_state(state, [_candidate("0xnew")], now=NOW)
    signals = detect_rank_signals(state, prev_addresses=set(), current_addresses={"0xnew"}, now=NOW)
    new = [s for s in signals if s["rule"] == "WHALE_NEW_ENTRANT"]
    assert new == []


def test_new_entrant_fires_only_once_per_whale():
    """After NEW_ENTRANT fires, subsequent runs don't re-fire it."""
    state = RankState()
    cands = [_candidate("0xnew")]
    total_new_entrant = 0
    for i in range(NEW_ENTRANT_MIN_CONSECUTIVE + 3):
        t = NOW + timedelta(hours=4 * i)
        update_ranks_state(state, cands, now=t)
        signals = detect_rank_signals(
            state,
            prev_addresses=set() if i == 0 else {"0xnew"},
            current_addresses={"0xnew"},
            now=t,
        )
        total_new_entrant += sum(1 for s in signals if s["rule"] == "WHALE_NEW_ENTRANT")
    assert total_new_entrant == 1


def test_drop_off_signal_for_established_whale_disappearing():
    """A whale present for DROP_OFF_MIN_RUNS_IN_TOP+ runs, now absent → DROP_OFF."""
    state = RankState()
    cands = [_candidate("0xveteran")]
    # build up history
    for i in range(DROP_OFF_MIN_RUNS_IN_TOP):
        update_ranks_state(state, cands, now=NOW + timedelta(hours=4 * i))
    # disappears next run
    next_time = NOW + timedelta(hours=4 * DROP_OFF_MIN_RUNS_IN_TOP)
    update_ranks_state(state, [], now=next_time)
    signals = detect_rank_signals(state, prev_addresses={"0xveteran"},
                                   current_addresses=set(), now=next_time)
    drop = [s for s in signals if s["rule"] == "WHALE_DROP_OFF"]
    assert len(drop) == 1
    assert drop[0]["address"] == "0xveteran"


def test_drop_off_no_signal_for_one_day_whale():
    """If whale was only there for 2 runs and disappears, no DROP_OFF (not 'established')."""
    state = RankState()
    update_ranks_state(state, [_candidate("0xnewbie")], now=NOW)
    update_ranks_state(state, [_candidate("0xnewbie")], now=NOW + timedelta(hours=4))
    update_ranks_state(state, [], now=NOW + timedelta(hours=8))
    signals = detect_rank_signals(state, prev_addresses={"0xnewbie"},
                                   current_addresses=set(), now=NOW + timedelta(hours=8))
    drop = [s for s in signals if s["rule"] == "WHALE_DROP_OFF"]
    assert drop == []


def test_drop_off_fires_only_once_per_whale():
    state = RankState()
    cands = [_candidate("0xveteran")]
    for i in range(DROP_OFF_MIN_RUNS_IN_TOP):
        update_ranks_state(state, cands, now=NOW + timedelta(hours=4 * i))
    # first absence
    t1 = NOW + timedelta(hours=4 * DROP_OFF_MIN_RUNS_IN_TOP)
    update_ranks_state(state, [], now=t1)
    detect_rank_signals(state, prev_addresses={"0xveteran"}, current_addresses=set(), now=t1)
    # still absent next run
    t2 = t1 + timedelta(hours=4)
    update_ranks_state(state, [], now=t2)
    signals2 = detect_rank_signals(state, prev_addresses=set(), current_addresses=set(), now=t2)
    drop = [s for s in signals2 if s["rule"] == "WHALE_DROP_OFF"]
    assert drop == []  # already announced


# ---------- history snapshot ----------

def test_append_history_writes_one_snapshot_per_run(tmp_path):
    history_path = tmp_path / "leaderboard_history.jsonl"
    cands = [_candidate(f"0x{i:040x}") for i in range(3)]
    append_history_snapshot(cands, history_path, run_ts=NOW)
    line = history_path.read_text().strip()
    parsed = json.loads(line)
    assert parsed["run_ts"] == NOW.isoformat()
    assert len(parsed["top"]) == 3
    assert parsed["top"][0]["rank"] == 1


def test_append_history_includes_key_metrics(tmp_path):
    history_path = tmp_path / "leaderboard_history.jsonl"
    cands = [_candidate("0xabc", pnl_month=500_000, account_value=2_000_000)]
    append_history_snapshot(cands, history_path, run_ts=NOW)
    parsed = json.loads(history_path.read_text().strip())
    entry = parsed["top"][0]
    assert entry["address"] == "0xabc"
    assert entry["pnl_month"] == 500_000
    assert entry["account_value"] == 2_000_000


def test_append_history_noop_on_empty_list(tmp_path):
    history_path = tmp_path / "leaderboard_history.jsonl"
    append_history_snapshot([], history_path, run_ts=NOW)
    # silent file — caller might pass empty during a leaderboard outage
    assert not history_path.exists()


# ---------- rotation ----------

def test_rotate_history_archives_prev_month(tmp_path):
    history_path = tmp_path / "leaderboard_history.jsonl"
    history_path.write_text('{"run_ts": "2026-04-15", "top": []}\n')
    import os
    april_ts = datetime(2026, 4, 15, tzinfo=timezone.utc).timestamp()
    os.utime(history_path, (april_ts, april_ts))
    rotated = rotate_history_if_month_changed(history_path, now=NOW)
    assert rotated is not None
    assert rotated.name == "leaderboard_history_2026-04.jsonl.gz"
    assert not history_path.exists()


def test_rotate_history_noop_in_current_month(tmp_path):
    history_path = tmp_path / "leaderboard_history.jsonl"
    history_path.write_text('{}\n')
    import os
    os.utime(history_path, (NOW.timestamp(), NOW.timestamp()))
    rotated = rotate_history_if_month_changed(history_path, now=NOW)
    assert rotated is None
    assert history_path.exists()


def test_cleanup_removes_archives_older_than_retention(tmp_path):
    old = tmp_path / "leaderboard_history_2026-01.jsonl.gz"
    recent = tmp_path / "leaderboard_history_2026-04.jsonl.gz"
    old.write_bytes(b"")
    recent.write_bytes(b"")
    import os
    jan_ts = datetime(2026, 1, 31, tzinfo=timezone.utc).timestamp()
    apr_ts = datetime(2026, 4, 30, tzinfo=timezone.utc).timestamp()
    os.utime(old, (jan_ts, jan_ts))
    os.utime(recent, (apr_ts, apr_ts))
    removed = cleanup_old_history_archives(tmp_path, retention_days=90, now=NOW)
    assert len(removed) == 1
    assert not old.exists()
    assert recent.exists()
