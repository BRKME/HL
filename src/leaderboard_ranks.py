"""Leaderboard rank tracking — surface NEW_ENTRANT and DROP_OFF signals.

Each whale_monitor run we pick top-N candidates. This module tracks each
address's presence over time so we can:
- alert when a NEW address has been in top for 3+ consecutive runs (12h)
  -> 'new active player', worth attention without panic-tracking pumps
- alert when an ESTABLISHED address (10+ runs in top) drops out
  -> 'capital pulled' or 'drawdown', context for past signals

Storage:
- state/leaderboard_ranks.json    rolling state per address
- state/leaderboard_history.jsonl one snapshot row per run (top-N, ranks)
                                  monthly rotated to .jsonl.gz, 90d retention

This module is pure persistence + bookkeeping. The whale_monitor wires
its signals into the existing pending/digest pipeline.
"""
from __future__ import annotations

import gzip
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.whale_source import WhaleCandidate


logger = logging.getLogger("leaderboard_ranks")

NEW_ENTRANT_MIN_CONSECUTIVE = 3   # ≈12 hours at 4h cadence
DROP_OFF_MIN_RUNS_IN_TOP = 10     # ≈40h of presence before they count as 'established'

_HISTORY_ARCHIVE_RE = re.compile(r"^leaderboard_history_(\d{4})-(\d{2})\.jsonl\.gz$")


# ----------------------------------------------------------------- models

@dataclass
class RankEntry:
    address: str
    first_seen_in_top_ms: int
    runs_in_top: int
    consecutive_in_top: int          # 0 means 'currently out of top'
    last_rank: int                   # 1-indexed
    last_seen_ms: int
    new_entrant_announced: bool = False
    drop_off_announced: bool = False


@dataclass
class RankState:
    entries: dict[str, RankEntry] = field(default_factory=dict)


# ------------------------------------------------------------------ load/save

def load_ranks_state(path: Path) -> RankState:
    path = Path(path)
    if not path.exists():
        return RankState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return RankState()
    entries: dict[str, RankEntry] = {}
    for addr, raw in (data.get("entries") or {}).items():
        if not isinstance(raw, dict):
            continue
        try:
            entries[addr] = RankEntry(
                address=str(addr),
                first_seen_in_top_ms=int(raw.get("first_seen_in_top_ms", 0)),
                runs_in_top=int(raw.get("runs_in_top", 0)),
                consecutive_in_top=int(raw.get("consecutive_in_top", 0)),
                last_rank=int(raw.get("last_rank", 0)),
                last_seen_ms=int(raw.get("last_seen_ms", 0)),
                new_entrant_announced=bool(raw.get("new_entrant_announced", False)),
                drop_off_announced=bool(raw.get("drop_off_announced", False)),
            )
        except (TypeError, ValueError):
            continue
    return RankState(entries=entries)


def save_ranks_state(state: RankState, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialised = {
        "entries": {
            addr: {
                "address": e.address,
                "first_seen_in_top_ms": e.first_seen_in_top_ms,
                "runs_in_top": e.runs_in_top,
                "consecutive_in_top": e.consecutive_in_top,
                "last_rank": e.last_rank,
                "last_seen_ms": e.last_seen_ms,
                "new_entrant_announced": e.new_entrant_announced,
                "drop_off_announced": e.drop_off_announced,
            }
            for addr, e in state.entries.items()
        }
    }
    path.write_text(json.dumps(serialised, indent=2), encoding="utf-8")


# ----------------------------------------------------------------- update

def update_ranks_state(
    state: RankState,
    candidates: list[WhaleCandidate],
    now: datetime,
) -> None:
    """Advance the per-address counters based on this run's top-N list.

    Whales present in the list get their counters incremented; whales
    absent from the list (but previously in state) have consecutive_in_top
    reset to 0, but their other counters preserved.

    Reset of drop_off_announced when whale RE-ENTERS top is intentional:
    we want to know if they leave again later.
    """
    now_ms = int(now.timestamp() * 1000)
    current_addresses = set()

    for rank_idx, c in enumerate(candidates):
        addr = c.address
        current_addresses.add(addr)
        existing = state.entries.get(addr)
        if existing is None:
            state.entries[addr] = RankEntry(
                address=addr,
                first_seen_in_top_ms=now_ms,
                runs_in_top=1,
                consecutive_in_top=1,
                last_rank=rank_idx + 1,
                last_seen_ms=now_ms,
            )
        else:
            existing.runs_in_top += 1
            existing.consecutive_in_top += 1 if existing.consecutive_in_top > 0 else 1
            # if consecutive was 0 (out of top), reset to 1 instead of cumulative
            if existing.consecutive_in_top != 1 and existing.last_seen_ms < now_ms - 1:
                # they're back after a gap — handled below in absent branch
                pass
            existing.last_rank = rank_idx + 1
            existing.last_seen_ms = now_ms
            # if they came back after dropping out — reset announce flag for next cycle
            if existing.drop_off_announced and existing.consecutive_in_top == 1:
                existing.drop_off_announced = False

    # whales that were in state but absent from this run
    for addr, entry in state.entries.items():
        if addr in current_addresses:
            continue
        if entry.consecutive_in_top > 0:
            # they were present last run and now absent — reset streak
            entry.consecutive_in_top = 0
            # also reset new_entrant_announced reset? No — once announced as
            # entrant, that's done. They can later trigger DROP_OFF.


def _reset_consecutive_on_return(state: RankState, addr: str, now_ms: int) -> None:
    """Helper: if address was absent for >= 1 prior run, force consecutive_in_top = 1."""
    e = state.entries[addr]
    # if there's a gap > 1 run window from last_seen, it's a re-entry
    # we don't track time gaps precisely here — handled in update logic


# ------------------------------------------------------------ signals

def detect_rank_signals(
    state: RankState,
    prev_addresses: set[str],
    current_addresses: set[str],
    now: datetime,
) -> list[dict]:
    """Return list of rank-change signal dicts for the pending/digest pipeline.

    Signals format (intentionally a plain dict — we don't reuse Signal here
    because rank signals carry whale-without-coin context, and the digest
    section gets its own renderer):
      {rule: 'WHALE_NEW_ENTRANT' | 'WHALE_DROP_OFF',
       address, runs_in_top, last_rank, ts_ms}
    """
    out: list[dict] = []
    now_ms = int(now.timestamp() * 1000)

    for addr, entry in state.entries.items():
        # NEW_ENTRANT: enough consecutive runs and not yet announced
        if (entry.consecutive_in_top >= NEW_ENTRANT_MIN_CONSECUTIVE
                and not entry.new_entrant_announced):
            out.append({
                "rule": "WHALE_NEW_ENTRANT",
                "address": addr,
                "runs_in_top": entry.runs_in_top,
                "consecutive_in_top": entry.consecutive_in_top,
                "last_rank": entry.last_rank,
                "ts_ms": now_ms,
            })
            entry.new_entrant_announced = True

        # DROP_OFF: was an established veteran, now absent
        if (entry.consecutive_in_top == 0
                and entry.runs_in_top >= DROP_OFF_MIN_RUNS_IN_TOP
                and not entry.drop_off_announced
                and addr in prev_addresses
                and addr not in current_addresses):
            out.append({
                "rule": "WHALE_DROP_OFF",
                "address": addr,
                "runs_in_top": entry.runs_in_top,
                "last_rank": entry.last_rank,
                "ts_ms": now_ms,
            })
            entry.drop_off_announced = True

    return out


# ------------------------------------------------------------ history snapshot

def append_history_snapshot(
    candidates: list[WhaleCandidate],
    history_path: Path,
    run_ts: datetime,
) -> None:
    """One JSONL line per run with the top-N snapshot.

    Empty candidates list -> noop (don't write garbage during outages).
    """
    if not candidates:
        return
    history_path = Path(history_path)
    history_path.parent.mkdir(parents=True, exist_ok=True)

    snapshot = {
        "run_ts": run_ts.isoformat(),
        "top": [
            {
                "rank": i + 1,
                "address": c.address,
                "display_name": c.display_name,
                "account_value": c.account_value,
                "pnl_day": c.pnl_day,
                "pnl_week": c.pnl_week,
                "pnl_month": c.pnl_month,
                "pnl_all_time": c.pnl_all_time,
                "roi_month": c.roi_month,
                "vlm_month": c.vlm_month,
            }
            for i, c in enumerate(candidates)
        ],
    }
    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(snapshot, separators=(",", ":")) + "\n")


# ------------------------------------------------------------ rotation

def rotate_history_if_month_changed(history_path: Path, now: datetime) -> Optional[Path]:
    history_path = Path(history_path)
    if not history_path.exists():
        return None
    mtime = datetime.fromtimestamp(history_path.stat().st_mtime, tz=timezone.utc)
    if (mtime.year, mtime.month) == (now.year, now.month):
        return None
    archive = history_path.parent / f"leaderboard_history_{mtime.year:04d}-{mtime.month:02d}.jsonl.gz"
    with history_path.open("rb") as src, gzip.open(archive, "wb") as dst:
        shutil.copyfileobj(src, dst)
    history_path.unlink()
    return archive


def cleanup_old_history_archives(
    dir_path: Path,
    retention_days: int = 90,
    now: Optional[datetime] = None,
) -> list[Path]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    removed: list[Path] = []
    if not dir_path.exists():
        return removed
    for child in dir_path.iterdir():
        if not child.is_file() or not _HISTORY_ARCHIVE_RE.match(child.name):
            continue
        mtime = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            try:
                child.unlink()
                removed.append(child)
            except OSError as e:
                logger.warning("could not remove %s: %s", child, e)
    return removed
