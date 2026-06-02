"""Whitelist daily verdicts — one Telegram message with a per-coin
LONG/SHORT/WAIT verdict for the focus coins (HYPE, BTC, ETH, NEAR, ZEC, TAO).

Reuses _compute_verdict from eth_focus to keep scoring logic consistent.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.eth_focus import _compute_verdict
from src.ta import compute_indicators


logger = logging.getLogger("whitelist_focus")

# The 6 coins to evaluate. Order = display order in the message.
FOCUS_COINS = ["HYPE", "BTC", "ETH", "NEAR", "ZEC", "TAO"]

_MOSCOW = timezone(timedelta(hours=3))


def _e(s: str) -> str:
    return html.escape(str(s))


def _ru_date(dt: datetime) -> str:
    months = ["янв", "фев", "мар", "апр", "мая", "июн",
              "июл", "авг", "сен", "окт", "ноя", "дек"]
    return f"{dt.day} {months[dt.month - 1]} {dt.year}"


def _fmt_price(p: float) -> str:
    if p is None or p == 0:
        return "—"
    if p >= 1000:
        return f"{round(p):,}".replace(",", " ")
    if p >= 1:
        return f"{round(p)}"
    if p >= 0.01:
        return f"{p:.4f}"
    return f"{p:.8f}".rstrip("0").rstrip(".")


def _read_recent_whale_fills(state_dir: Path, coin: str, days: int,
                              now: datetime) -> list[dict]:
    """Read whale fills from state for ONE coin over last N days."""
    import json
    path = state_dir / "whale_fills.jsonl"
    if not path.exists():
        return []
    cutoff_ms = int((now - timedelta(days=days)).timestamp() * 1000)
    out = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("coin") != coin:
                    continue
                if row.get("time_ms", 0) < cutoff_ms:
                    continue
                out.append(row)
    except OSError:
        return []
    return out


def _read_recent_whale_signals(state_dir: Path, coin: str, days: int,
                                now: datetime) -> list[dict]:
    """Read whale signals from state for ONE coin over last N days."""
    import json
    path = state_dir / "whale_signals.jsonl"
    if not path.exists():
        return []
    cutoff = now - timedelta(days=days)
    out = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("coin") != coin:
                    continue
                ts_str = row.get("run_ts", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    continue
                if ts < cutoff:
                    continue
                out.append(row)
    except OSError:
        return []
    return out


def _whale_state_for_coin(state_dir: Path, coin: str, now: datetime
                           ) -> tuple[int, Optional[bool]]:
    """Return (cluster_count, whale_net_long) for the verdict input."""
    signals = _read_recent_whale_signals(state_dir, coin, days=7, now=now)
    fills = _read_recent_whale_fills(state_dir, coin, days=7, now=now)
    cluster_count = sum(1 for s in signals if s.get("rule") == "WHALE_CLUSTER")
    net_long: Optional[bool] = None
    if fills:
        long_notional = sum(f.get("notional_usd", 0) for f in fills
                            if f.get("direction") == "Open Long")
        short_notional = sum(f.get("notional_usd", 0) for f in fills
                              if f.get("direction") == "Open Short")
        if long_notional > short_notional * 1.2:
            net_long = True
        elif short_notional > long_notional * 1.2:
            net_long = False
    return cluster_count, net_long


def evaluate_coin(
    coin: str,
    mark: float,
    candles_closes: Optional[list[float]],
    funding_apr_pct: Optional[float],
    regime_snapshot: Optional[dict],
    state_dir: Path,
    now: datetime,
) -> tuple[str, str]:
    """Compute (verdict, rationale) for one coin. Returns ('WAIT', ...)
    if data is too thin to decide."""
    if not mark or mark <= 0:
        return ("WAIT", "Нет цены")

    ta_dict = None
    if candles_closes and len(candles_closes) >= 200:
        candle_dicts = [{"o": c, "h": c, "l": c, "c": c} for c in candles_closes]
        ta_dict = compute_indicators(candle_dicts, swing_lookback=30)

    cluster_count, whale_net_long = _whale_state_for_coin(state_dir, coin, now)

    regime = (regime_snapshot or {}).get("regime") if regime_snapshot else None
    phase = (((regime_snapshot or {}).get("cycle") or {}).get("phase")
             if regime_snapshot else None)

    return _compute_verdict(
        ta=ta_dict,
        funding_apr_pct=funding_apr_pct,
        whale_net_long=whale_net_long,
        whale_cluster_count=cluster_count,
        regime=regime,
        phase=phase,
    )


def compute_all_verdicts(
    now: datetime,
    coin_data: dict[str, dict],
    regime_snapshot: Optional[dict],
    state_dir: Path,
) -> list[tuple[str, float, str, str]]:
    """Compute verdicts for all focus coins.

    Returns [(coin, mark, verdict, rationale)] in FOCUS_COINS order.
    Coins with no mark get verdict='NODATA' and rationale=''.

    This is the single source of truth for verdict computation —
    both render_whitelist_verdicts and the verdict journal consume it,
    so the journaled verdict always matches what the user sees.
    """
    out: list[tuple[str, float, str, str]] = []
    for coin in FOCUS_COINS:
        data = coin_data.get(coin, {})
        mark = data.get("mark", 0)
        if not mark or mark <= 0:
            out.append((coin, 0.0, "NODATA", ""))
            continue
        verdict, rationale = evaluate_coin(
            coin=coin,
            mark=mark,
            candles_closes=data.get("candles_closes"),
            funding_apr_pct=data.get("funding_apr_pct"),
            regime_snapshot=regime_snapshot,
            state_dir=state_dir,
            now=now,
        )
        out.append((coin, mark, verdict, rationale))
    return out


def render_whitelist_verdicts(
    now: datetime,
    coin_data: dict[str, dict],
    regime_snapshot: Optional[dict],
    state_dir: Path,
) -> str:
    """One-message report.

    coin_data: {coin: {mark, candles_closes, funding_apr_pct}}.
    Each coin gets one line: emoji COIN price - verdict (reasons).
    """
    msk = now.astimezone(_MOSCOW)
    header = (f"🎯 <b>Whitelist daily</b> — {_ru_date(msk)}, "
              f"{msk.strftime('%H:%M')} MSK")
    emoji_map = {"LONG": "🟢", "SHORT": "🔴", "WAIT": "⚪"}
    label_map = {"LONG": "ВХОДИТЬ LONG", "SHORT": "ВХОДИТЬ SHORT", "WAIT": "НЕ ВХОДИТЬ"}

    # Add regime advice headline if available (same as daily-monitor)
    regime = (regime_snapshot or {}).get("regime") if regime_snapshot else None
    phase = (((regime_snapshot or {}).get("cycle") or {}).get("phase")
             if regime_snapshot else None)
    regime_line = ""
    if regime and phase:
        regime_line = f"\n<i>regime {_e(regime)} · phase {_e(phase)}</i>"

    lines = [header + regime_line, ""]

    verdicts = compute_all_verdicts(now, coin_data, regime_snapshot, state_dir)
    for coin, mark, verdict, rationale in verdicts:
        if verdict == "NODATA":
            lines.append(f"⚫ <code>{_e(coin)}</code> — нет данных")
            continue

        emoji = emoji_map.get(verdict, "⚪")
        label = label_map.get(verdict, "НЕ ВХОДИТЬ")

        # Compact: parenthesised rationale, trimmed to keep one line short
        short_rat = rationale
        if len(short_rat) > 90:
            short_rat = short_rat[:87].rstrip() + "…"

        lines.append(
            f"{emoji} <code>{_e(coin)}</code> ${_fmt_price(mark)} — "
            f"<b>{label}</b>  <i>({short_rat})</i>"
        )

    return "\n".join(lines)
