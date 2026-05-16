"""Tests for src/whale_tracker.py — fetch fills incrementally, store, rotate."""
import gzip
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.whale_tracker import (
    WhaleFill,
    FillCursor,
    parse_fill,
    load_cursor,
    save_cursor,
    write_fills,
    rotate_if_month_changed,
    cleanup_old_archives,
    fetch_whale_fills,
)


NOW = datetime(2026, 5, 15, 10, 0, tzinfo=timezone.utc)


# ---------- parsing ----------

SAMPLE_FILL = {
    "closedPnl": "0.0",
    "coin": "BTC",
    "crossed": True,
    "dir": "Open Long",
    "hash": "0xa166e3fa63",
    "oid": 90542681,
    "px": "63500.0",
    "side": "B",
    "startPosition": "0.0",
    "sz": "0.5",
    "time": 1747300800000,  # 2026-05-15 ~08:00 UTC
    "fee": "0.05",
    "feeToken": "USDC",
    "tid": 118906512037719,
}


def test_parse_fill_extracts_all_fields():
    f = parse_fill(SAMPLE_FILL, whale="0xabc123")
    assert f.whale == "0xabc123"
    assert f.coin == "BTC"
    assert f.side == "B"
    assert f.direction == "Open Long"
    assert f.size == pytest.approx(0.5)
    assert f.price == pytest.approx(63500.0)
    assert f.notional_usd == pytest.approx(31750.0)  # 0.5 * 63500
    assert f.tid == 118906512037719
    assert f.time_ms == 1747300800000
    assert f.closed_pnl == pytest.approx(0.0)


def test_parse_fill_handles_short_direction():
    raw = {**SAMPLE_FILL, "dir": "Open Short", "side": "A", "sz": "0.3"}
    f = parse_fill(raw, whale="0xabc")
    assert f.direction == "Open Short"
    assert f.side == "A"


def test_parse_fill_skips_malformed():
    """Bad numbers should not crash — return None to be filtered."""
    bad = {"coin": "BTC", "px": "garbage", "sz": "0.5", "tid": 1, "time": 1, "dir": "Open Long", "side": "B"}
    f = parse_fill(bad, whale="0xabc")
    assert f is None


def test_parse_fill_handles_hip3_coin_prefix():
    """HIP-3 coins have 'dex:NAME' format — keep as-is, downstream filters."""
    raw = {**SAMPLE_FILL, "coin": "xyz:XYZ100"}
    f = parse_fill(raw, whale="0xabc")
    assert f.coin == "xyz:XYZ100"


# ---------- cursor ----------

def test_load_cursor_missing_file_returns_empty(tmp_path):
    cursor = load_cursor(tmp_path / "nonexistent.json")
    assert cursor.last_tid_by_whale == {}


def test_save_and_load_cursor_roundtrip(tmp_path):
    path = tmp_path / "cursor.json"
    cursor = FillCursor(last_tid_by_whale={"0xabc": 100, "0xdef": 200})
    save_cursor(cursor, path)
    loaded = load_cursor(path)
    assert loaded.last_tid_by_whale == {"0xabc": 100, "0xdef": 200}


def test_load_cursor_handles_corrupt_file(tmp_path):
    path = tmp_path / "cursor.json"
    path.write_text("not json")
    cursor = load_cursor(path)
    assert cursor.last_tid_by_whale == {}


def test_cursor_advances_to_latest_tid_per_whale(tmp_path):
    path = tmp_path / "cursor.json"
    cursor = FillCursor(last_tid_by_whale={"0xabc": 100})
    cursor.advance("0xabc", 250)
    cursor.advance("0xabc", 200)  # older — should NOT regress
    cursor.advance("0xdef", 50)   # new whale
    assert cursor.last_tid_by_whale["0xabc"] == 250
    assert cursor.last_tid_by_whale["0xdef"] == 50


# ---------- write fills ----------

def _fill(whale="0xabc", coin="BTC", tid=1, time_ms=NOW.timestamp() * 1000) -> WhaleFill:
    return WhaleFill(
        whale=whale, coin=coin, side="B", direction="Open Long",
        size=1.0, price=63000.0, notional_usd=63000.0,
        tid=int(tid), time_ms=int(time_ms),
        closed_pnl=0.0, crossed=True, oid=12345,
    )


def test_write_fills_appends_to_jsonl(tmp_path):
    path = tmp_path / "whale_fills.jsonl"
    write_fills([_fill(tid=1), _fill(tid=2)], path)
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert {p["tid"] for p in parsed} == {1, 2}


def test_write_fills_appends_not_overwrites(tmp_path):
    path = tmp_path / "whale_fills.jsonl"
    write_fills([_fill(tid=1)], path)
    write_fills([_fill(tid=2)], path)
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 2


def test_write_fills_empty_list_is_noop(tmp_path):
    """No fills -> don't touch the file (don't even create empty)."""
    path = tmp_path / "whale_fills.jsonl"
    write_fills([], path)
    assert not path.exists()


# ---------- monthly rotation ----------

def test_rotate_creates_gz_when_current_file_is_from_previous_month(tmp_path):
    """File last modified in April, current month is May -> rotate."""
    path = tmp_path / "whale_fills.jsonl"
    path.write_text('{"tid": 1, "time_ms": 1714000000000}\n')  # April 2026
    import os
    april_ts = datetime(2026, 4, 15, tzinfo=timezone.utc).timestamp()
    os.utime(path, (april_ts, april_ts))
    rotated = rotate_if_month_changed(path, now=NOW)
    assert rotated is not None
    assert rotated.exists()
    assert rotated.name == "whale_fills_2026-04.jsonl.gz"
    assert not path.exists()  # original cleared


def test_rotate_preserves_content_in_archive(tmp_path):
    path = tmp_path / "whale_fills.jsonl"
    path.write_text('{"tid": 1}\n{"tid": 2}\n')
    # force "last modified" to April by stomping mtime to a date in April
    import os
    april_ts = datetime(2026, 4, 15, tzinfo=timezone.utc).timestamp()
    os.utime(path, (april_ts, april_ts))
    rotated = rotate_if_month_changed(path, now=NOW)
    with gzip.open(rotated, "rt") as fh:
        content = fh.read()
    assert content.count("\n") == 2


def test_rotate_noop_when_same_month(tmp_path):
    path = tmp_path / "whale_fills.jsonl"
    path.write_text('{"tid": 1}\n')
    # mtime "today" — same month as NOW
    import os
    os.utime(path, (NOW.timestamp(), NOW.timestamp()))
    result = rotate_if_month_changed(path, now=NOW)
    assert result is None
    assert path.exists()  # unchanged


def test_rotate_noop_when_file_missing(tmp_path):
    path = tmp_path / "missing.jsonl"
    result = rotate_if_month_changed(path, now=NOW)
    assert result is None


# ---------- cleanup old archives ----------

def test_cleanup_removes_archives_older_than_retention(tmp_path):
    """Default retention 90 days — anything older deleted."""
    old = tmp_path / "whale_fills_2026-01.jsonl.gz"
    recent = tmp_path / "whale_fills_2026-04.jsonl.gz"
    old.write_bytes(b"")
    recent.write_bytes(b"")
    import os
    jan_ts = datetime(2026, 1, 31, tzinfo=timezone.utc).timestamp()
    apr_ts = datetime(2026, 4, 30, tzinfo=timezone.utc).timestamp()
    os.utime(old, (jan_ts, jan_ts))
    os.utime(recent, (apr_ts, apr_ts))

    removed = cleanup_old_archives(tmp_path, retention_days=90, now=NOW)
    assert len(removed) == 1
    assert old.name in {r.name for r in removed}
    assert not old.exists()
    assert recent.exists()


def test_cleanup_ignores_non_archive_files(tmp_path):
    """Only files matching whale_fills_*.jsonl.gz are eligible."""
    junk = tmp_path / "decisions.jsonl"
    junk.write_text("[]")
    import os
    very_old = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(junk, (very_old, very_old))
    removed = cleanup_old_archives(tmp_path, retention_days=90, now=NOW)
    assert removed == []
    assert junk.exists()


# ---------- fetch_whale_fills (the integration tying it together) ----------

def test_fetch_whale_fills_uses_userFillsByTime_when_cursor_present():
    """If we have a cursor for this whale, query from cursor's time forward."""
    client = MagicMock()
    client.get_user_fills_by_time.return_value = [SAMPLE_FILL]

    cursor = FillCursor(last_tid_by_whale={"0xabc": 100})
    fills = fetch_whale_fills(client, "0xabc", cursor, now=NOW)
    assert len(fills) == 1
    # the call must use userFillsByTime, not full userFills
    client.get_user_fills_by_time.assert_called_once()
    args, kwargs = client.get_user_fills_by_time.call_args
    assert kwargs.get("address") == "0xabc" or "0xabc" in args


def test_fetch_whale_fills_uses_full_history_for_new_whale():
    """No cursor -> bootstrap with last 4 hours via userFillsByTime from NOW - 4h."""
    client = MagicMock()
    client.get_user_fills_by_time.return_value = [SAMPLE_FILL]
    cursor = FillCursor()
    fills = fetch_whale_fills(client, "0xnew", cursor, now=NOW, bootstrap_hours=4)
    client.get_user_fills_by_time.assert_called_once()
    kwargs = client.get_user_fills_by_time.call_args.kwargs
    # start_time_ms is roughly NOW - 4h
    four_hours_ago_ms = int((NOW - timedelta(hours=4)).timestamp() * 1000)
    assert abs(kwargs["start_time_ms"] - four_hours_ago_ms) < 5000


def test_fetch_whale_fills_filters_out_already_seen_tids():
    """Cursor at tid=100. Response has tids 99, 100, 101 — keep only 101."""
    client = MagicMock()
    client.get_user_fills_by_time.return_value = [
        {**SAMPLE_FILL, "tid": 99},
        {**SAMPLE_FILL, "tid": 100},
        {**SAMPLE_FILL, "tid": 101},
    ]
    cursor = FillCursor(last_tid_by_whale={"0xabc": 100})
    fills = fetch_whale_fills(client, "0xabc", cursor, now=NOW)
    assert [f.tid for f in fills] == [101]


def test_fetch_whale_fills_returns_empty_on_client_error():
    """Don't let one whale's failure kill the whole tracker run."""
    client = MagicMock()
    client.get_user_fills_by_time.side_effect = RuntimeError("HL hiccup")
    cursor = FillCursor()
    fills = fetch_whale_fills(client, "0xabc", cursor, now=NOW)
    assert fills == []


def test_fetch_whale_fills_advances_cursor_on_success():
    """Cursor must advance to the max tid seen."""
    client = MagicMock()
    client.get_user_fills_by_time.return_value = [
        {**SAMPLE_FILL, "tid": 201},
        {**SAMPLE_FILL, "tid": 203},
        {**SAMPLE_FILL, "tid": 202},
    ]
    cursor = FillCursor(last_tid_by_whale={"0xabc": 100})
    fetch_whale_fills(client, "0xabc", cursor, now=NOW)
    assert cursor.last_tid_by_whale["0xabc"] == 203
