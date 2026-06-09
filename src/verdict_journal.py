"""Verdict journal — append-only log of every bot verdict.

Each verdict (from whitelist_focus and eth_focus runners) gets appended
as a JSON line to state/verdict_journal.jsonl. After 2-3 weeks of
accumulation a separate backtester can read this and compute the
actual effectiveness of bot recommendations: 'LONG verdicts on ETH
had WR 67% / 24h, avg +2.1%' — direct answer to 'does the model work'.

Schema (one line per coin per run):
{
  "ts": "2026-06-02T06:05:00+00:00",     # ISO timestamp of the run
  "source": "whitelist_focus" | "eth_focus",
  "coin": "ETH",
  "mark": 1976.0,                        # price at verdict time
  "verdict": "LONG" | "SHORT" | "WAIT",
  "rationale": "За long: тренд вверх, ...",
  "regime": "BEAR" | null,
  "phase": "CAPITULATION" | null
}

Append-only: never rewrite or delete. If file is corrupted, just append
to it — backtester is responsible for parsing what it can.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


logger = logging.getLogger("verdict_journal")


@dataclass(frozen=True)
class VerdictEntry:
    ts: datetime
    source: str            # "whitelist_focus" | "eth_focus" | "daily_monitor"
    coin: str
    mark: float
    verdict: str           # "LONG" | "SHORT" | "WAIT" — verdict_final
    rationale: str
    regime: Optional[str] = None
    phase: Optional[str] = None
    # Analyst review June 9: also record verdict WITHOUT regime/phase
    # so the backtester can compare raw vs final WR. If regime layer
    # adds no edge (or hurts), we drop it.
    verdict_raw: Optional[str] = None
    rationale_raw: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "ts": self.ts.astimezone(timezone.utc).isoformat(),
            "source": self.source,
            "coin": self.coin,
            "mark": float(self.mark),
            "verdict": self.verdict,
            "rationale": self.rationale,
            "regime": self.regime,
            "phase": self.phase,
        }
        # Optional fields — only written if set, keeps old entries readable
        if self.verdict_raw is not None:
            d["verdict_raw"] = self.verdict_raw
        if self.rationale_raw is not None:
            d["rationale_raw"] = self.rationale_raw
        return d


def append_verdicts(journal_path: Path, entries: list[VerdictEntry]) -> int:
    """Append verdict entries to JSONL. Creates file/dir if missing.

    Returns count of successfully written entries. Per-entry failures
    are logged but don't break the whole batch.
    """
    if not entries:
        return 0
    journal_path = Path(journal_path)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with journal_path.open("a", encoding="utf-8") as fh:
            for e in entries:
                try:
                    fh.write(json.dumps(e.to_dict(), ensure_ascii=False) + "\n")
                    written += 1
                except (TypeError, ValueError) as err:
                    logger.warning("Failed to serialise verdict %s: %s", e, err)
    except OSError as e:
        logger.warning("Failed to open journal %s for append: %s", journal_path, e)
        return 0
    return written


def load_verdicts(journal_path: Path,
                   since: Optional[datetime] = None) -> list[VerdictEntry]:
    """Read verdicts from JSONL.

    Skips malformed lines silently (best-effort parsing — we never want
    a corrupted line to break the read).
    """
    journal_path = Path(journal_path)
    if not journal_path.exists():
        return []
    out: list[VerdictEntry] = []
    try:
        with journal_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_str = row.get("ts", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    continue
                if since and ts < since:
                    continue
                try:
                    out.append(VerdictEntry(
                        ts=ts,
                        source=str(row.get("source", "")),
                        coin=str(row.get("coin", "")),
                        mark=float(row.get("mark") or 0),
                        verdict=str(row.get("verdict", "")),
                        rationale=str(row.get("rationale", "")),
                        regime=row.get("regime"),
                        phase=row.get("phase"),
                        verdict_raw=row.get("verdict_raw"),
                        rationale_raw=row.get("rationale_raw"),
                    ))
                except (TypeError, ValueError):
                    continue
    except OSError:
        return []
    return out
