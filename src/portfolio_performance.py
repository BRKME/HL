"""Portfolio performance — HL `portfolio` endpoint.

Gives us PnL by period (day/week/month/allTime) across all 3 wallets without
needing to bootstrap a baseline ourselves. HL computes these server-side.

Layout of the response from POST /info {"type":"portfolio","user":"0x..."}:
  [
    ["day",      {accountValueHistory: [[ts,val],...], pnlHistory: [[ts,val],...], vlm}],
    ["week",     {...}],
    ["month",    {...}],
    ["allTime",  {...}],
    ["perpDay",  {...}],  # perp-only variants — we ignore for now
    ...
  ]

The last entry of accountValueHistory[period] is the current value (or near it);
the last entry of pnlHistory[period] is the realised+unrealised PnL accrued
within that period.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests

from src.hl_api import HyperliquidError


INFO_URL = "https://api.hyperliquid.xyz/info"
DEFAULT_TIMEOUT = 15

# The periods we surface in the daily report. perp-only periods are noisy
# (most of our positions are perp anyway, and 'day' combined is what users
# expect to see).
PORTFOLIO_PERIODS = ("day", "week", "month", "allTime")


# ----------------------------------------------------------------- models

@dataclass(frozen=True)
class PeriodStats:
    period: str          # "day" | "week" | "month" | "allTime"
    pnl: float           # final PnL accrued within the period
    start_value: float   # first account value point of the period
    end_value: float     # last account value point of the period
    vlm: float           # volume traded within the period
    roi_pct: float       # pnl / start_value * 100 (0 when start_value <= 0)


@dataclass
class PerformanceSnapshot:
    address: str
    day: PeriodStats
    week: PeriodStats
    month: PeriodStats
    all_time: PeriodStats
    current_account_value: float
    failed_wallets: list[str] = field(default_factory=list)


# ----------------------------------------------------------------- HTTP

def _post(payload: dict[str, Any]) -> Any:
    try:
        r = requests.post(INFO_URL, json=payload, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as e:
        raise HyperliquidError(f"portfolio fetch failed: {e}") from e


def fetch_portfolio(address: str) -> list:
    """One wallet's portfolio response. Address is lowercased automatically."""
    return _post({"type": "portfolio", "user": address.lower()})


# ----------------------------------------------------------------- parsing

def _to_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _zero_period(name: str) -> PeriodStats:
    return PeriodStats(period=name, pnl=0.0, start_value=0.0,
                       end_value=0.0, vlm=0.0, roi_pct=0.0)


def _parse_period(period_name: str, data: dict | None) -> PeriodStats:
    if not isinstance(data, dict):
        return _zero_period(period_name)

    pnl_history = data.get("pnlHistory") or []
    av_history = data.get("accountValueHistory") or []
    vlm = _to_float(data.get("vlm"))

    pnl = _to_float(pnl_history[-1][1]) if pnl_history else 0.0
    start_value = _to_float(av_history[0][1]) if av_history else 0.0
    end_value = _to_float(av_history[-1][1]) if av_history else 0.0

    roi_pct = (pnl / start_value * 100) if start_value > 0 else 0.0

    return PeriodStats(
        period=period_name,
        pnl=pnl,
        start_value=start_value,
        end_value=end_value,
        vlm=vlm,
        roi_pct=roi_pct,
    )


def parse_portfolio_response(response: list, address: str) -> PerformanceSnapshot:
    """Convert raw HL portfolio response into a PerformanceSnapshot.

    Defensive against missing periods, empty arrays, and garbage numbers.
    """
    by_period: dict[str, dict] = {}
    if isinstance(response, list):
        for item in response:
            if isinstance(item, list) and len(item) == 2:
                name, data = item
                by_period[str(name)] = data if isinstance(data, dict) else {}

    day = _parse_period("day", by_period.get("day"))
    week = _parse_period("week", by_period.get("week"))
    month = _parse_period("month", by_period.get("month"))
    all_time = _parse_period("allTime", by_period.get("allTime"))

    # current account value = end of 'day' (most granular & recent)
    current = day.end_value if day.end_value > 0 else all_time.end_value

    return PerformanceSnapshot(
        address=address,
        day=day,
        week=week,
        month=month,
        all_time=all_time,
        current_account_value=current,
    )


# ----------------------------------------------------------------- aggregation

def _sum_period(period_name: str, snaps: list[PerformanceSnapshot]) -> PeriodStats:
    """Sum PnL and start_value across wallets; ROI = totalPnl / totalStart."""
    total_pnl = 0.0
    total_start = 0.0
    total_end = 0.0
    total_vlm = 0.0
    for s in snaps:
        ps = getattr(s, "day" if period_name == "day"
                    else "week" if period_name == "week"
                    else "month" if period_name == "month"
                    else "all_time")
        total_pnl += ps.pnl
        total_start += ps.start_value
        total_end += ps.end_value
        total_vlm += ps.vlm
    roi = (total_pnl / total_start * 100) if total_start > 0 else 0.0
    return PeriodStats(
        period=period_name, pnl=total_pnl, start_value=total_start,
        end_value=total_end, vlm=total_vlm, roi_pct=roi,
    )


def fetch_combined_performance(addresses: list[str]) -> PerformanceSnapshot:
    """Fetch portfolio for each wallet and sum into a combined snapshot.

    Never raises — wallets that fail are listed in failed_wallets.
    """
    snaps: list[PerformanceSnapshot] = []
    failed: list[str] = []
    for addr in addresses:
        addr_lc = addr.lower()
        try:
            raw = _post({"type": "portfolio", "user": addr_lc})
            snaps.append(parse_portfolio_response(raw, address=addr_lc))
        except Exception:
            failed.append(addr_lc)

    if not snaps:
        return PerformanceSnapshot(
            address="combined",
            day=_zero_period("day"),
            week=_zero_period("week"),
            month=_zero_period("month"),
            all_time=_zero_period("allTime"),
            current_account_value=0.0,
            failed_wallets=failed,
        )

    return PerformanceSnapshot(
        address="combined",
        day=_sum_period("day", snaps),
        week=_sum_period("week", snaps),
        month=_sum_period("month", snaps),
        all_time=_sum_period("allTime", snaps),
        current_account_value=sum(s.current_account_value for s in snaps),
        failed_wallets=failed,
    )
