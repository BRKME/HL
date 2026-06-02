"""Tests for verdict_journal — append-only verdict log."""
import json
from datetime import datetime, timedelta, timezone

from src.verdict_journal import (
    VerdictEntry, append_verdicts, load_verdicts,
)


NOW = datetime(2026, 6, 2, 6, 5, tzinfo=timezone.utc)


def _entry(coin="ETH", verdict="LONG", source="whitelist_focus", ts=NOW):
    return VerdictEntry(
        ts=ts, source=source, coin=coin, mark=2000.0,
        verdict=verdict, rationale="Test rationale",
        regime="BEAR", phase="CAPITULATION",
    )


def test_append_creates_file_when_missing(tmp_path):
    path = tmp_path / "state" / "verdict_journal.jsonl"
    n = append_verdicts(path, [_entry()])
    assert n == 1
    assert path.exists()
    assert path.read_text(encoding="utf-8").count("\n") == 1


def test_append_appends_to_existing_file(tmp_path):
    path = tmp_path / "verdict_journal.jsonl"
    append_verdicts(path, [_entry(coin="ETH")])
    append_verdicts(path, [_entry(coin="BTC"), _entry(coin="HYPE")])
    content = path.read_text(encoding="utf-8")
    assert content.count("\n") == 3
    assert "ETH" in content
    assert "BTC" in content
    assert "HYPE" in content


def test_append_empty_list_no_op(tmp_path):
    path = tmp_path / "verdict_journal.jsonl"
    n = append_verdicts(path, [])
    assert n == 0
    assert not path.exists()


def test_entry_serialised_with_iso_timestamp(tmp_path):
    path = tmp_path / "j.jsonl"
    append_verdicts(path, [_entry()])
    line = path.read_text().strip()
    row = json.loads(line)
    assert "2026-06-02T06:05:00+00:00" in row["ts"]
    assert row["verdict"] == "LONG"
    assert row["regime"] == "BEAR"
    assert row["phase"] == "CAPITULATION"


def test_load_returns_entries_in_file_order(tmp_path):
    path = tmp_path / "j.jsonl"
    e1 = _entry(coin="ETH", ts=NOW)
    e2 = _entry(coin="BTC", ts=NOW + timedelta(hours=1))
    e3 = _entry(coin="HYPE", ts=NOW + timedelta(hours=2))
    append_verdicts(path, [e1, e2, e3])
    loaded = load_verdicts(path)
    assert [v.coin for v in loaded] == ["ETH", "BTC", "HYPE"]


def test_load_filters_by_since(tmp_path):
    path = tmp_path / "j.jsonl"
    old = _entry(coin="ETH", ts=NOW - timedelta(days=30))
    recent = _entry(coin="BTC", ts=NOW - timedelta(days=1))
    append_verdicts(path, [old, recent])
    loaded = load_verdicts(path, since=NOW - timedelta(days=7))
    assert len(loaded) == 1
    assert loaded[0].coin == "BTC"


def test_load_missing_file_returns_empty(tmp_path):
    assert load_verdicts(tmp_path / "missing.jsonl") == []


def test_load_skips_malformed_lines(tmp_path):
    path = tmp_path / "j.jsonl"
    append_verdicts(path, [_entry()])
    # corrupt: prepend a bad line
    raw = path.read_text()
    path.write_text("not json\n" + raw + "{\"ts\": \"bad-date\"}\n")
    loaded = load_verdicts(path)
    assert len(loaded) == 1
    assert loaded[0].coin == "ETH"


def test_round_trip_preserves_all_fields(tmp_path):
    path = tmp_path / "j.jsonl"
    e = VerdictEntry(
        ts=NOW, source="eth_focus", coin="ETH", mark=2034.5,
        verdict="SHORT", rationale="trend down", regime="BULL", phase="EUPHORIA",
    )
    append_verdicts(path, [e])
    loaded = load_verdicts(path)[0]
    assert loaded.source == "eth_focus"
    assert loaded.coin == "ETH"
    assert loaded.mark == 2034.5
    assert loaded.verdict == "SHORT"
    assert loaded.rationale == "trend down"
    assert loaded.regime == "BULL"
    assert loaded.phase == "EUPHORIA"


def test_null_regime_and_phase_supported(tmp_path):
    """When OracAI failed, regime/phase will be None — must survive round-trip."""
    path = tmp_path / "j.jsonl"
    e = VerdictEntry(
        ts=NOW, source="eth_focus", coin="ETH", mark=2000,
        verdict="WAIT", rationale="no data", regime=None, phase=None,
    )
    append_verdicts(path, [e])
    loaded = load_verdicts(path)[0]
    assert loaded.regime is None
    assert loaded.phase is None
