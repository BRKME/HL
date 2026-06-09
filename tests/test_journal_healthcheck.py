"""Tests for the journal healthcheck script.

We test the script as a black box — invoke it via subprocess on a tmp
journal file, check exit code and output keywords. The script is meant
to be run manually so behaviour stability matters more than internals.
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "journal_healthcheck.py"


def _write_journal(repo: Path, entries: list[dict]) -> None:
    state = repo / "state"
    state.mkdir(parents=True, exist_ok=True)
    with (state / "verdict_journal.jsonl").open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _run(repo: Path) -> tuple[int, str]:
    """Run healthcheck against a fake repo with journal in repo/state/."""
    # Script resolves repo as Path(__file__).resolve().parent.parent
    # We copy the script into repo/scripts/ so it points at repo/state/
    scripts_dir = repo / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    target = scripts_dir / "journal_healthcheck.py"
    target.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(target)],
        capture_output=True, text=True, timeout=30,
    )
    return result.returncode, result.stdout


def _entry(ts: datetime, coin="ETH", source="whitelist_focus",
           verdict="LONG", regime="BULL", phase="MID_BULL",
           verdict_raw=None, rs_30d=None, rs_90d=None) -> dict:
    d = {
        "ts": ts.astimezone(timezone.utc).isoformat(),
        "source": source, "coin": coin, "mark": 2000,
        "verdict": verdict, "rationale": "test",
        "regime": regime, "phase": phase,
    }
    if verdict_raw is not None:
        d["verdict_raw"] = verdict_raw
    if rs_30d is not None:
        d["rs_30d"] = rs_30d
    if rs_90d is not None:
        d["rs_90d"] = rs_90d
    return d


def test_empty_journal_exits_with_status_1(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "verdict_journal.jsonl").write_text("")
    code, out = _run(tmp_path)
    assert code == 1
    assert "EMPTY" in out


def test_missing_journal_exits_with_status_1(tmp_path):
    code, out = _run(tmp_path)
    assert code == 1
    assert "EMPTY" in out


def test_healthy_journal_exits_with_status_0(tmp_path):
    """Recent entries, raw + RS recorded, multiple phases → no warnings."""
    now = datetime.now(timezone.utc)
    entries = []
    for i in range(7):
        ts = now - timedelta(days=i)
        phase = "MID_BULL" if i % 2 else "LATE_BEAR"  # variance
        for coin in ("ETH", "BTC"):
            entries.append(_entry(
                ts=ts, coin=coin, regime="BULL", phase=phase,
                verdict_raw="LONG", rs_30d=12.5, rs_90d=-5.0,
            ))
    _write_journal(tmp_path, entries)
    code, out = _run(tmp_path)
    assert "OK — no critical issues" in out
    assert code == 0


def test_no_raw_verdict_triggers_warning(tmp_path):
    now = datetime.now(timezone.utc)
    entries = [_entry(ts=now - timedelta(days=i), coin="ETH",
                       regime="BULL", phase="MID_BULL",
                       rs_30d=5.0)
               for i in range(3)]
    _write_journal(tmp_path, entries)
    code, out = _run(tmp_path)
    assert code == 2
    assert "verdict_raw never recorded" in out


def test_no_rs_triggers_warning(tmp_path):
    now = datetime.now(timezone.utc)
    entries = [_entry(ts=now - timedelta(days=i), coin="ETH",
                       regime="BULL", phase="MID_BULL",
                       verdict_raw="LONG")
               for i in range(3)]
    _write_journal(tmp_path, entries)
    code, out = _run(tmp_path)
    assert code == 2
    assert "RS never recorded" in out


def test_single_phase_triggers_warning(tmp_path):
    now = datetime.now(timezone.utc)
    entries = [_entry(ts=now - timedelta(days=i),
                       phase="ACCUMULATION",
                       verdict_raw="LONG", rs_30d=5.0)
               for i in range(7)]
    _write_journal(tmp_path, entries)
    code, out = _run(tmp_path)
    assert code == 2
    assert "Only one phase observed" in out


def test_stale_journal_triggers_warning(tmp_path):
    """Last entry > 2 days old → bot may have stopped writing."""
    old = datetime.now(timezone.utc) - timedelta(days=5)
    entries = [_entry(ts=old, verdict_raw="LONG", rs_30d=5.0,
                       phase="MID_BULL")]
    _write_journal(tmp_path, entries)
    code, out = _run(tmp_path)
    assert code == 2
    assert "days old" in out


def test_disagreement_count_reported(tmp_path):
    """When raw differs from final, healthcheck reports it."""
    now = datetime.now(timezone.utc)
    entries = []
    for i in range(7):
        # raw says LONG but regime blocks → final WAIT
        entries.append(_entry(
            ts=now - timedelta(days=i), coin="ETH",
            verdict="WAIT", verdict_raw="LONG",
            regime="BEAR", phase="MID_BEAR" if i % 2 else "EARLY_BEAR",
            rs_30d=5.0,
        ))
    _write_journal(tmp_path, entries)
    code, out = _run(tmp_path)
    assert "regime blocks raw entry" in out
    # Should report 7 blocks
    assert "7" in out
