"""Whale candidate source — HL public leaderboard.

The leaderboard is published as a static JSON file at
  https://stats-data.hyperliquid.xyz/Mainnet/leaderboard
(no signing, no Info POST — just GET). It contains every active trader with
their windowed performance: day / week / month / allTime.

We pick candidates by combining filters that screen out:
- small accounts (<$100k account value)
- accounts with no real recent activity (vlm_month too low)
- accounts whose entire allTime PnL is one spike (allTime / month ≈ 1)
- accounts that lost money over the last 30 days

What survives goes into the rotating curated list. Win-rate scoring per
candidate happens later (whale_scoring.py) by replaying their fills.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
DEFAULT_TIMEOUT = 30


class WhaleSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class WhaleCandidate:
    address: str
    display_name: str
    account_value: float

    pnl_day: float
    pnl_week: float
    pnl_month: float
    pnl_all_time: float

    vlm_day: float
    vlm_week: float
    vlm_month: float
    vlm_all_time: float

    roi_day: float
    roi_week: float
    roi_month: float
    roi_all_time: float


@dataclass(frozen=True)
class CandidateFilters:
    min_account_value: float = 100_000.0   # filters retail
    min_pnl_month: float = 50_000.0        # has to have made real money in 30d
    min_vlm_month_usd: float = 5_000_000.0  # has to have actually traded in 30d
    spike_ratio_min: float = 1.5           # allTime / month >= 1.5 -> not a one-spike trader
    top_n: int = 50


def _to_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _norm_addr(addr: str) -> str:
    return addr.lower() if isinstance(addr, str) else ""


def parse_leaderboard_entry(entry: dict) -> WhaleCandidate:
    """Convert one leaderboard row into a WhaleCandidate.

    windowPerformances is a list of [window_name, {pnl, roi, vlm}] tuples.
    """
    perfs = {w[0]: w[1] for w in (entry.get("windowPerformances") or []) if isinstance(w, list) and len(w) == 2}

    def _get(window: str, field: str) -> float:
        return _to_float((perfs.get(window) or {}).get(field, 0))

    return WhaleCandidate(
        address=_norm_addr(entry.get("ethAddress", "")),
        display_name=str(entry.get("displayName") or ""),
        account_value=_to_float(entry.get("accountValue")),
        pnl_day=_get("day", "pnl"),
        pnl_week=_get("week", "pnl"),
        pnl_month=_get("month", "pnl"),
        pnl_all_time=_get("allTime", "pnl"),
        vlm_day=_get("day", "vlm"),
        vlm_week=_get("week", "vlm"),
        vlm_month=_get("month", "vlm"),
        vlm_all_time=_get("allTime", "vlm"),
        roi_day=_get("day", "roi"),
        roi_week=_get("week", "roi"),
        roi_month=_get("month", "roi"),
        roi_all_time=_get("allTime", "roi"),
    )


def fetch_leaderboard() -> list[WhaleCandidate]:
    """GET the public leaderboard. Returns [] on unexpected shape, raises on network error."""
    try:
        resp = requests.get(LEADERBOARD_URL, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError, Exception) as e:
        raise WhaleSourceError(f"leaderboard fetch failed: {e}") from e

    rows = data.get("leaderboardRows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []

    out: list[WhaleCandidate] = []
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(parse_leaderboard_entry(entry))
        except (TypeError, ValueError, KeyError):
            continue
    return out


def pick_candidates(
    candidates: list[WhaleCandidate],
    filters: CandidateFilters,
) -> list[WhaleCandidate]:
    """Apply quality filters and return top N by 30-day PnL."""
    survivors: list[WhaleCandidate] = []
    for c in candidates:
        if c.account_value < filters.min_account_value:
            continue
        if c.pnl_month < filters.min_pnl_month:
            continue
        if c.vlm_month < filters.min_vlm_month_usd:
            continue
        # spike check: allTime PnL should be meaningfully greater than month
        # (otherwise their entire history fits inside the last 30d → one-spike trader)
        if c.pnl_month > 0:
            ratio = c.pnl_all_time / c.pnl_month
            if ratio < filters.spike_ratio_min:
                continue
        survivors.append(c)

    survivors.sort(key=lambda c: c.pnl_month, reverse=True)
    return survivors[: filters.top_n]
