"""Loader for decisions.jsonl — the historical trade log written by main.py.

Each line is one weekly run. A run may produce zero (SKIP/EXIT) or many (STRONG 60/40)
picks. We flatten picks into individual Decision objects for the matcher to use.

Format reference (from main.py / scoring.py):
{
  "ts": "2026-05-09T07:54:54+00:00",
  "signal": "STRONG" | "MODERATE" | "SKIP" | "EXIT",
  "leverage": 1,
  "picks": [
    {
      "symbol": "BTC",
      "hl_symbol": "BTC",      # the coin name as HL sees it (matches HL API)
      "entry": 80188.0,        # entry price recommended
      "alloc_usd": 200.0,      # dollar allocation
      "sl_price": 75201.6,     # recommended stop-loss price
      "sl_pct": -6.22,
      "sl_method": "atr",
      "atr14": 1994.55,
      ...
    },
    ...
  ],
  ...
}
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Decision:
    """One recommended trade. Side is 'long' — weekly bot doesn't short."""
    ts: datetime
    signal: str            # STRONG | MODERATE (SKIP/EXIT produce no Decision)
    coin: str              # HL symbol, e.g. "BTC"
    entry: float           # recommended entry price
    alloc_usd: float       # dollar allocation
    expected_size: float   # coin units = alloc_usd / entry
    sl_price: float        # recommended stop-loss
    sl_pct: float          # SL as percent (negative)
    sl_method: str         # "atr" | "swing" | "floor"
    atr14: float           # 14-day ATR at decision time
    side: str = "long"
    regime_at_entry: Optional[str] = None    # OracAI regime when trade was recommended
    phase_at_entry: Optional[str] = None     # OracAI cycle.phase when trade was recommended


def parse_decision_row(row: dict) -> list[Decision]:
    """Flatten one decisions.jsonl row into individual Decision objects.

    Returns [] for SKIP/EXIT rows (empty picks) or malformed rows.
    """
    picks = row.get("picks") or []
    if not picks:
        return []

    try:
        ts = datetime.fromisoformat(row["ts"])
    except (KeyError, ValueError):
        return []

    signal = row.get("signal", "")
    oracai_data = row.get("oracai") or {}
    regime_at_entry = oracai_data.get("regime")
    phase_at_entry = oracai_data.get("phase")
    out: list[Decision] = []
    for p in picks:
        try:
            entry = float(p["entry"])
            alloc_usd = float(p["alloc_usd"])
            if entry <= 0 or alloc_usd <= 0:
                continue
            coin = p.get("hl_symbol") or p.get("symbol")
            if not coin:
                continue
            out.append(Decision(
                ts=ts,
                signal=signal,
                coin=coin,
                entry=entry,
                alloc_usd=alloc_usd,
                expected_size=alloc_usd / entry,
                sl_price=float(p.get("sl_price", 0) or 0),
                sl_pct=float(p.get("sl_pct", 0) or 0),
                sl_method=str(p.get("sl_method", "atr")),
                atr14=float(p.get("atr14", 0) or 0),
                regime_at_entry=regime_at_entry,
                phase_at_entry=phase_at_entry,
            ))
        except (KeyError, TypeError, ValueError):
            # skip individual malformed picks but continue with the rest
            continue
    return out


def load_decisions(
    path: Path,
    lookback_days: Optional[int] = None,
    now: Optional[datetime] = None,
) -> list[Decision]:
    """Read decisions.jsonl, flatten, and optionally filter by recency.

    Missing file -> []. Corrupt lines skipped silently (bot must keep running).
    """
    path = Path(path)
    if not path.exists():
        return []

    cutoff: Optional[datetime] = None
    if lookback_days is not None:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=lookback_days)

    decisions: list[Decision] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            for d in parse_decision_row(row):
                if cutoff is None or d.ts >= cutoff:
                    decisions.append(d)
    return decisions
