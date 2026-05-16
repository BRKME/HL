"""Whale fills tracker — incremental fetch + persistent storage.

Pipeline per whale per run:
  1. Look up cursor (last_tid we've seen for this whale)
  2. Query userFillsByTime since cursor's timestamp (or last 4h if new whale)
  3. Filter out fills with tid <= cursor's last_tid (dedup)
  4. Persist new fills to state/whale_fills.jsonl
  5. Advance cursor to max(seen tids)

Storage layout:
  state/whale_cursor.json          {last_tid_by_whale: {addr: tid, ...}}
  state/whale_fills.jsonl          current month, append-only
  state/whale_fills_YYYY-MM.jsonl.gz  archived months, gzipped
  -> archives older than 90 days are removed at the start of each run

The tracker is purely persistence — correlation/alerting lives elsewhere.
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
from typing import Any, Optional


logger = logging.getLogger("whale_tracker")

_ARCHIVE_RE = re.compile(r"^whale_fills_(\d{4})-(\d{2})\.jsonl\.gz$")


# --------------------------------------------------------------- data model

@dataclass(frozen=True)
class WhaleFill:
    whale: str
    coin: str
    side: str             # "B" (buy) | "A" (ask/sell)
    direction: str        # "Open Long" | "Close Long" | "Open Short" | "Close Short"
    size: float
    price: float
    notional_usd: float
    tid: int
    time_ms: int
    closed_pnl: float
    crossed: bool
    oid: int

    def to_json_line(self) -> str:
        return json.dumps({
            "whale": self.whale,
            "coin": self.coin,
            "side": self.side,
            "direction": self.direction,
            "size": self.size,
            "price": self.price,
            "notional_usd": self.notional_usd,
            "tid": self.tid,
            "time_ms": self.time_ms,
            "closed_pnl": self.closed_pnl,
            "crossed": self.crossed,
            "oid": self.oid,
        }, separators=(",", ":"))


@dataclass
class FillCursor:
    last_tid_by_whale: dict[str, int] = field(default_factory=dict)

    def advance(self, whale: str, tid: int) -> None:
        prev = self.last_tid_by_whale.get(whale, 0)
        if tid > prev:
            self.last_tid_by_whale[whale] = tid

    def last_tid(self, whale: str) -> int:
        return self.last_tid_by_whale.get(whale, 0)


# ------------------------------------------------------------------ parsing

def parse_fill(raw: dict, whale: str) -> Optional[WhaleFill]:
    """Parse one HL fill row; return None on malformed input (so caller can skip)."""
    try:
        size = float(raw["sz"])
        price = float(raw["px"])
        tid = int(raw["tid"])
        time_ms = int(raw["time"])
        if size <= 0 or price <= 0:
            return None
        return WhaleFill(
            whale=whale,
            coin=str(raw["coin"]),
            side=str(raw.get("side", "")),
            direction=str(raw.get("dir", "")),
            size=size,
            price=price,
            notional_usd=size * price,
            tid=tid,
            time_ms=time_ms,
            closed_pnl=_to_float(raw.get("closedPnl", 0)),
            crossed=bool(raw.get("crossed", False)),
            oid=int(raw.get("oid", 0)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _to_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------------- cursor

def load_cursor(path: Path) -> FillCursor:
    path = Path(path)
    if not path.exists():
        return FillCursor()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return FillCursor()
    last_by_whale = {
        str(k): int(v) for k, v in (data.get("last_tid_by_whale") or {}).items()
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit())
    }
    return FillCursor(last_tid_by_whale=last_by_whale)


def save_cursor(cursor: FillCursor, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"last_tid_by_whale": cursor.last_tid_by_whale}, indent=2),
        encoding="utf-8",
    )


# ----------------------------------------------------------------- storage

def write_fills(fills: list[WhaleFill], path: Path) -> None:
    """Append fills to JSONL; noop if list is empty."""
    if not fills:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for f in fills:
            fh.write(f.to_json_line() + "\n")


# ------------------------------------------------------------ rotation/cleanup

def rotate_if_month_changed(path: Path, now: datetime) -> Optional[Path]:
    """If the current jsonl was last touched in a previous month, gzip-archive it
    and clear the original. Returns the path to the new archive, or None if noop.
    """
    path = Path(path)
    if not path.exists():
        return None
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    if (mtime.year, mtime.month) == (now.year, now.month):
        return None
    archive_name = f"whale_fills_{mtime.year:04d}-{mtime.month:02d}.jsonl.gz"
    archive_path = path.parent / archive_name
    with path.open("rb") as src, gzip.open(archive_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    path.unlink()
    return archive_path


def cleanup_old_archives(
    dir_path: Path,
    retention_days: int = 90,
    now: Optional[datetime] = None,
) -> list[Path]:
    """Remove whale_fills_YYYY-MM.jsonl.gz archives older than retention_days.

    Uses the file's mtime, not the YYYY-MM in the name, for safety against
    misnamed files.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    removed: list[Path] = []
    if not dir_path.exists():
        return removed
    for child in dir_path.iterdir():
        if not child.is_file() or not _ARCHIVE_RE.match(child.name):
            continue
        mtime = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            try:
                child.unlink()
                removed.append(child)
            except OSError as e:
                logger.warning("could not remove %s: %s", child, e)
    return removed


# ---------------------------------------------------------- incremental fetch

def fetch_whale_fills(
    client,
    whale: str,
    cursor: FillCursor,
    now: datetime,
    bootstrap_hours: int = 4,
    safety_margin_minutes: int = 30,
) -> list[WhaleFill]:
    """Fetch new fills for one whale since cursor's last_tid (or last 4h if new).

    Advances the cursor on success. Never raises — returns [] on any failure
    so one bad whale doesn't poison the run.
    """
    whale_lc = whale.lower()
    last_tid = cursor.last_tid(whale_lc)

    if last_tid == 0:
        start_ms = int((now - timedelta(hours=bootstrap_hours)).timestamp() * 1000)
    else:
        # Cursor's last fill time isn't in the cursor — we go back a bit and dedup by tid.
        # The safety margin handles clock skew + GH Actions delay.
        start_ms = int((now - timedelta(hours=bootstrap_hours,
                                        minutes=safety_margin_minutes)).timestamp() * 1000)

    try:
        raw_fills = client.get_user_fills_by_time(
            address=whale_lc,
            start_time_ms=start_ms,
        )
    except Exception as e:
        logger.warning("fills fetch failed for %s: %s", whale_lc[:10], e)
        return []

    if not isinstance(raw_fills, list):
        return []

    parsed: list[WhaleFill] = []
    max_tid = last_tid
    for raw in raw_fills:
        f = parse_fill(raw, whale=whale_lc)
        if f is None:
            continue
        if f.tid <= last_tid:
            continue
        parsed.append(f)
        if f.tid > max_tid:
            max_tid = f.tid

    if max_tid > last_tid:
        cursor.advance(whale_lc, max_tid)

    return parsed
