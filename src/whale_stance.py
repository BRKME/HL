"""Whale stance — aggregate per-coin long/short bias from whale fills.

For each focus coin, sum 'Open Long' and 'Open Short' notional over a
recent window. The ratio tells us 'what whales are doing on this coin
right now' — a directional signal that complements TA/funding/regime.

Not a perfect proxy for net exposure (close events from earlier longs
aren't subtracted from short side), but it captures recent activity
direction which is what we want for a short-term stance read.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


logger = logging.getLogger("whale_stance")


@dataclass(frozen=True)
class WhaleStance:
    coin: str
    long_notional: float
    short_notional: float
    long_count: int
    short_count: int

    @property
    def total(self) -> float:
        return self.long_notional + self.short_notional

    @property
    def long_pct(self) -> float:
        if self.total <= 0:
            return 0.0
        return self.long_notional / self.total * 100

    @property
    def bias(self) -> Optional[str]:
        """Return 'long' if ≥60% long, 'short' if ≤40% long, else None.
        60/40 split = clear directional bias; in between = noise."""
        if self.total <= 0 or (self.long_count + self.short_count) < 3:
            return None
        if self.long_pct >= 60:
            return "long"
        if self.long_pct <= 40:
            return "short"
        return None


def compute_stance(
    state_dir: Path,
    coins: list[str],
    now: datetime,
    lookback_days: int = 7,
    min_notional_usd: float = 10_000.0,
) -> dict[str, WhaleStance]:
    """Read whale_fills.jsonl, aggregate Open Long/Open Short per coin.

    min_notional_usd: skip fills below this (consistent with the FLIP
    filter fix — only count meaningful whale activity, not micro-fills).

    Returns {coin: WhaleStance} for every requested coin (even ones
    with zero activity, so caller can show '—').
    """
    path = state_dir / "whale_fills.jsonl"
    out: dict[str, dict] = {
        c: {"long": 0.0, "short": 0.0, "lc": 0, "sc": 0} for c in coins
    }
    if not path.exists():
        return {c: WhaleStance(c, 0, 0, 0, 0) for c in coins}

    cutoff_ms = int((now - timedelta(days=lookback_days)).timestamp() * 1000)
    coin_set = set(coins)
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                coin = row.get("coin")
                if coin not in coin_set:
                    continue
                if row.get("time_ms", 0) < cutoff_ms:
                    continue
                notional = float(row.get("notional_usd") or 0)
                if notional < min_notional_usd:
                    continue
                direction = row.get("direction")
                bucket = out[coin]
                if direction == "Open Long":
                    bucket["long"] += notional
                    bucket["lc"] += 1
                elif direction == "Open Short":
                    bucket["short"] += notional
                    bucket["sc"] += 1
    except OSError:
        pass

    return {
        c: WhaleStance(
            coin=c,
            long_notional=v["long"],
            short_notional=v["short"],
            long_count=v["lc"],
            short_count=v["sc"],
        )
        for c, v in out.items()
    }


def format_stance_line(stances: dict[str, WhaleStance],
                        coins_order: list[str]) -> Optional[str]:
    """One-line block: '🐋 Киты 7d: BTC 72%↑ • ETH —  • ZEC 91%↑ ...'

    Coins with zero activity show '—'. Coins with directional bias
    show 'XX%↑' (long) or 'XX%↓' (short). Coins with mixed activity
    (no clear bias) show 'XX% mix'.

    Returns None when none of the coins has meaningful activity.
    """
    if not stances:
        return None

    bits = []
    any_data = False
    for c in coins_order:
        s = stances.get(c)
        total_events = (s.long_count + s.short_count) if s else 0
        if s is None or s.total <= 0 or total_events < 3:
            # 0 events OR <3 events = sample too small to call a bias
            bits.append(f"{c} —")
            continue
        any_data = True
        bias = s.bias
        if bias == "long":
            bits.append(f"{c} {s.long_pct:.0f}%↑")
        elif bias == "short":
            bits.append(f"{c} {(100-s.long_pct):.0f}%↓")
        else:
            bits.append(f"{c} {s.long_pct:.0f}% mix")

    if not any_data:
        return None
    return "🐋 Киты 7d: " + " • ".join(bits)
