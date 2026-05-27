"""Portfolio model and aggregation across multiple HL wallets.

Perp and spot are kept separate (different instruments, different risk).
Within perp, positions for the same coin across wallets net out (long + short
cancel; same-side sums); entry is weighted by abs(size).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


# --------------------------------------------------------------- data models

@dataclass(frozen=True)
class PerpPosition:
    coin: str
    size: float                # signed: positive long, negative short
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: int
    leverage_type: str         # 'cross' | 'isolated'
    liquidation_price: float
    margin_used: float
    position_value: float
    return_on_equity: float
    funding_since_open: float
    account: str               # wallet label

    @property
    def side(self) -> str:
        if self.size > 0:
            return "long"
        if self.size < 0:
            return "short"
        return "flat"

    @property
    def notional_usd(self) -> float:
        return abs(self.size) * self.mark_price


@dataclass(frozen=True)
class SpotPosition:
    coin: str
    total: float
    hold: float
    entry_notional: float       # entryNtl: total cost basis in USDC
    account: str

    @property
    def avg_entry(self) -> Optional[float]:
        if self.total > 0 and self.entry_notional > 0:
            return self.entry_notional / self.total
        return None


@dataclass(frozen=True)
class AggregatedPerpPosition:
    coin: str
    net_size: float                              # signed net across wallets
    weighted_entry: float                        # by abs(size)
    total_pnl: float
    contributors: list[tuple[str, float]]        # [(account, size), ...]
    avg_leverage: float
    max_liquidation_distance_pct: float          # for the riskiest wallet

    @property
    def side(self) -> str:
        if self.net_size > 1e-9:
            return "long"
        if self.net_size < -1e-9:
            return "short"
        return "flat"


@dataclass
class Portfolio:
    perp: list[AggregatedPerpPosition] = field(default_factory=list)
    spot: list[SpotPosition] = field(default_factory=list)
    total_account_value: float = 0.0
    wallets_seen: list[str] = field(default_factory=list)
    # Per-wallet account value (perp marginSummary). Used by the daily
    # monitor to show 'Кошельки: 1 $X • Marta $Y • Arkadii $Z' line.
    wallet_values: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_raw(
        cls,
        raw_responses: dict[str, dict],
        spot_resolver: Callable[[str], str],
        perp_dust_usd: float = 10.0,
        spot_dust_usd: float = 10.0,
    ) -> "Portfolio":
        """Build a Portfolio from {wallet_label: {'perp': ..., 'spot': ...}}."""
        perp_positions: list[PerpPosition] = []
        spot_positions: list[SpotPosition] = []
        total_value = 0.0
        wallet_values: dict[str, float] = {}

        for label, data in raw_responses.items():
            perp_raw = data.get("perp", {})
            spot_raw = data.get("spot", {})

            # account value from perp summary (spot value is in balances)
            margin = perp_raw.get("marginSummary", {}) or {}
            try:
                wallet_val = float(margin.get("accountValue", 0) or 0)
            except (TypeError, ValueError):
                wallet_val = 0.0
            wallet_values[label] = wallet_val
            total_value += wallet_val

            for ap in perp_raw.get("assetPositions", []) or []:
                pos = ap.get("position")
                if not pos:
                    continue
                try:
                    perp_positions.append(parse_perp_position(pos, account=label))
                except (TypeError, ValueError, KeyError):
                    # bad row — skip silently rather than failing the whole run
                    continue

            for bal in spot_raw.get("balances", []) or []:
                try:
                    s = parse_spot_balance(bal, account=label, resolver=spot_resolver)
                except (TypeError, ValueError, KeyError):
                    continue
                if s.coin == "USDC":
                    continue
                # dust filter: cost-basis notional (or current value if known later)
                if s.entry_notional < spot_dust_usd:
                    continue
                spot_positions.append(s)

        aggregated_perp = aggregate_perp(perp_positions, dust_usd=perp_dust_usd)

        return cls(
            perp=aggregated_perp,
            spot=spot_positions,
            total_account_value=total_value,
            wallets_seen=list(raw_responses.keys()),
            wallet_values=wallet_values,
        )


# ------------------------------------------------------------------ parsing

def parse_perp_position(raw: dict, account: str) -> PerpPosition:
    """Parse a single 'position' dict from clearinghouseState assetPositions."""
    size = float(raw["szi"])
    entry = float(raw["entryPx"])
    position_value = float(raw.get("positionValue", 0) or 0)
    abs_size = abs(size)
    mark = position_value / abs_size if abs_size > 0 else entry

    lev = raw.get("leverage", {}) or {}
    funding = raw.get("cumFunding", {}) or {}

    return PerpPosition(
        coin=raw["coin"],
        size=size,
        entry_price=entry,
        mark_price=mark,
        unrealized_pnl=float(raw.get("unrealizedPnl", 0) or 0),
        leverage=int(lev.get("value", 1) or 1),
        leverage_type=str(lev.get("type", "cross")),
        liquidation_price=float(raw.get("liquidationPx", 0) or 0),
        margin_used=float(raw.get("marginUsed", 0) or 0),
        position_value=position_value,
        return_on_equity=float(raw.get("returnOnEquity", 0) or 0),
        funding_since_open=float(funding.get("sinceOpen", 0) or 0),
        account=account,
    )


def parse_spot_balance(raw: dict, account: str, resolver: Callable[[str], str]) -> SpotPosition:
    """Parse one balance row; resolve coin via spotMeta mapper."""
    raw_coin = raw["coin"]
    resolved = resolver(raw_coin)
    return SpotPosition(
        coin=resolved,
        total=float(raw.get("total", 0) or 0),
        hold=float(raw.get("hold", 0) or 0),
        entry_notional=float(raw.get("entryNtl", 0) or 0),
        account=account,
    )


# --------------------------------------------------------------- aggregation

def aggregate_perp(
    positions: list[PerpPosition],
    dust_usd: float = 10.0,
) -> list[AggregatedPerpPosition]:
    """Group perp positions by coin, net signed size, weight entry by |size|."""
    by_coin: dict[str, list[PerpPosition]] = {}
    for p in positions:
        # dust filter at individual-position level
        if p.notional_usd < dust_usd:
            continue
        by_coin.setdefault(p.coin, []).append(p)

    out: list[AggregatedPerpPosition] = []
    for coin, group in by_coin.items():
        net_size = sum(p.size for p in group)
        abs_weight = sum(abs(p.size) for p in group)
        if abs_weight == 0:
            continue
        weighted_entry = sum(abs(p.size) * p.entry_price for p in group) / abs_weight
        total_pnl = sum(p.unrealized_pnl for p in group)
        avg_lev = sum(p.leverage * abs(p.size) for p in group) / abs_weight

        # liquidation distance — smallest distance among wallets (worst case)
        distances = []
        for p in group:
            if p.liquidation_price > 0 and p.mark_price > 0:
                if p.side == "long":
                    d = (p.mark_price - p.liquidation_price) / p.mark_price
                else:
                    d = (p.liquidation_price - p.mark_price) / p.mark_price
                distances.append(d * 100)
        worst_distance = min(distances) if distances else 0.0

        out.append(AggregatedPerpPosition(
            coin=coin,
            net_size=net_size,
            weighted_entry=weighted_entry,
            total_pnl=total_pnl,
            contributors=[(p.account, p.size) for p in group],
            avg_leverage=avg_lev,
            max_liquidation_distance_pct=worst_distance,
        ))

    return out
