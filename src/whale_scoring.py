"""Whale scoring — win-rate and PnL stats per whale over rolling 30d window.

Data source priority:
  1. state/whale_fills.jsonl — what the tracker has been accumulating.
     Cheap, no network, but the first week or so JSONL is sparse.
  2. fallback: HL userFillsByTime(now - 30d) — single API call per whale.

A whale needs MIN_CLOSED_TRADES (10) to get an "ok" score. Below that we
return status='insufficient_data' and downstream code skips them as a sign
source. Per-coin stats need at least 5 trades on that coin to surface.

What counts as a "closed trade": fills where closed_pnl != 0. Open Long/Short
fills don't have realised PnL and aren't useful for win-rate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from src.whale_tracker import WhaleFill, parse_fill


MIN_CLOSED_TRADES = 10
MIN_COIN_TRADES = 5
DEFAULT_WINDOW_DAYS = 30

INSUFFICIENT_DATA = "insufficient_data"
OK = "ok"


@dataclass(frozen=True)
class CoinStats:
    coin: str
    closed_trades: int
    win_rate: float
    total_pnl: float
    avg_pnl: float


@dataclass
class WhaleScore:
    whale: str
    status: str                       # "ok" | "insufficient_data"
    closed_trades: int
    win_rate: float                   # 0..1
    total_pnl: float
    avg_pnl: float
    best_trade: float
    worst_trade: float
    window_days: int
    by_coin: dict[str, CoinStats] = field(default_factory=dict)


# ----------------------------------------------------------------- scoring

def _score_one_group(fills: list[WhaleFill]) -> tuple[int, float, float, float, float, float]:
    """Return (closed_trades, win_rate, total_pnl, avg_pnl, best, worst)."""
    closed = [f for f in fills if f.closed_pnl != 0.0]
    n = len(closed)
    if n == 0:
        return (0, 0.0, 0.0, 0.0, 0.0, 0.0)
    wins = sum(1 for f in closed if f.closed_pnl > 0)
    total = sum(f.closed_pnl for f in closed)
    pnls = [f.closed_pnl for f in closed]
    return (n, wins / n, total, total / n, max(pnls), min(pnls))


def score_from_fills(
    fills: list[WhaleFill],
    whale: str,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> WhaleScore:
    """Compute win-rate and PnL stats. Doesn't touch I/O."""
    whale_lc = whale.lower()
    cutoff_ms = int((now - timedelta(days=window_days)).timestamp() * 1000)

    in_window = [
        f for f in fills
        if f.whale == whale_lc and f.time_ms >= cutoff_ms
    ]

    n, wr, tot, avg, best, worst = _score_one_group(in_window)

    if n < MIN_CLOSED_TRADES:
        return WhaleScore(
            whale=whale_lc, status=INSUFFICIENT_DATA,
            closed_trades=n, win_rate=wr, total_pnl=tot, avg_pnl=avg,
            best_trade=best, worst_trade=worst, window_days=window_days,
        )

    by_coin: dict[str, CoinStats] = {}
    coins = {f.coin for f in in_window if f.closed_pnl != 0.0}
    for coin in coins:
        coin_fills = [f for f in in_window if f.coin == coin]
        cn, cwr, ctot, cavg, _, _ = _score_one_group(coin_fills)
        if cn >= MIN_COIN_TRADES:
            by_coin[coin] = CoinStats(
                coin=coin, closed_trades=cn, win_rate=cwr,
                total_pnl=ctot, avg_pnl=cavg,
            )

    return WhaleScore(
        whale=whale_lc, status=OK,
        closed_trades=n, win_rate=wr, total_pnl=tot, avg_pnl=avg,
        best_trade=best, worst_trade=worst, window_days=window_days,
        by_coin=by_coin,
    )


# ------------------------------------------------------------------- loading

def _load_jsonl_fills(path: Path) -> list[WhaleFill]:
    """Read whale_fills.jsonl into WhaleFill objects. Corrupt lines skipped."""
    if not path.exists():
        return []
    out: list[WhaleFill] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = _line_to_fill(line)
            except (ValueError, KeyError, TypeError):
                continue
            if row is not None:
                out.append(row)
    return out


def _line_to_fill(line: str) -> Optional[WhaleFill]:
    """Reverse of WhaleFill.to_json_line()."""
    import json
    d = json.loads(line)
    try:
        return WhaleFill(
            whale=str(d["whale"]),
            coin=str(d["coin"]),
            side=str(d["side"]),
            direction=str(d["direction"]),
            size=float(d["size"]),
            price=float(d["price"]),
            notional_usd=float(d["notional_usd"]),
            tid=int(d["tid"]),
            time_ms=int(d["time_ms"]),
            closed_pnl=float(d["closed_pnl"]),
            crossed=bool(d.get("crossed", False)),
            oid=int(d.get("oid", 0)),
        )
    except (KeyError, ValueError, TypeError):
        return None


def load_fills_for_whale(
    whale: str,
    state_dir: Path,
    client: Any,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> list[WhaleFill]:
    """Return fills for `whale` in the window, from JSONL or HL API fallback.

    Falls back to client.get_user_fills_by_time when JSONL has fewer than
    MIN_CLOSED_TRADES closed trades in the window.
    """
    whale_lc = whale.lower()
    state_dir = Path(state_dir)
    jsonl_path = state_dir / "whale_fills.jsonl"

    all_fills = _load_jsonl_fills(jsonl_path)
    cutoff_ms = int((now - timedelta(days=window_days)).timestamp() * 1000)
    whale_fills = [
        f for f in all_fills
        if f.whale == whale_lc and f.time_ms >= cutoff_ms
    ]
    closed_count = sum(1 for f in whale_fills if f.closed_pnl != 0.0)

    if closed_count >= MIN_CLOSED_TRADES:
        return whale_fills

    # Fallback: pull last 30d directly from HL
    try:
        raw = client.get_user_fills_by_time(
            address=whale_lc,
            start_time_ms=cutoff_ms,
        ) or []
    except Exception:
        return whale_fills  # JSONL data is better than nothing

    api_fills: list[WhaleFill] = []
    for r in raw:
        f = parse_fill(r, whale=whale_lc)
        if f is not None:
            api_fills.append(f)

    # de-dup by tid against JSONL (avoid double-counting if some overlap)
    seen_tids = {f.tid for f in whale_fills}
    api_only = [f for f in api_fills if f.tid not in seen_tids]
    return whale_fills + api_only


def score_whale(
    whale: str,
    state_dir: Path,
    client: Any,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> WhaleScore:
    """End-to-end: load fills (JSONL + fallback) and compute the score."""
    fills = load_fills_for_whale(whale, state_dir, client, now=now, window_days=window_days)
    return score_from_fills(fills, whale=whale, now=now, window_days=window_days)
