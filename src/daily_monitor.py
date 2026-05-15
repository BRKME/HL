"""Daily HL portfolio monitor — entry point.

Pipeline:
  1. Load 3 wallet addresses from whitelist.yaml
  2. Fetch perp + spot state for each via HL Info API
  3. Aggregate into a single Portfolio (perp coalesced by coin across wallets)
  4. Load decisions.jsonl (14-day window) and match positions to decisions
  5. Fetch current + yesterday OracAI snapshot
  6. Fetch current marks for held coins via HL metaAndAssetCtxs
  7. Evaluate rule engine to produce Alerts
  8. Render report and send to Telegram

Designed for graceful degradation: if any single source fails (one wallet,
OracAI, marks), the bot still sends a partial report rather than nothing.
"""
from __future__ import annotations

import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from src.daily_report import render_daily_report
from src.decisions_log import load_decisions
from src.hl_api import fetch_meta_and_ctxs
from src.hl_client import HLClient
from src.matcher import match_positions
from src.monitor_rules import evaluate_all, RuleConfig
from src.oracai import fetch_snapshot as fetch_oracai_snapshot
from src.oracai_history import fetch_snapshot_days_ago
from src.portfolio import Portfolio
from src.telegram_sender import send_messages, alert_owner


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WHITELIST = REPO_ROOT / "whitelist.yaml"
DEFAULT_DECISIONS = REPO_ROOT / "decisions.jsonl"
DECISION_LOOKBACK_DAYS = 14

logger = logging.getLogger("daily_monitor")


def load_accounts(path: Path) -> list[dict]:
    """Read whitelist.yaml `accounts:` section.

    Format:
      accounts:
        - address: "0x..."
          label: main
        - address: "0x..."
          label: second
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return []
    accs = data.get("accounts") or []
    out: list[dict] = []
    for a in accs:
        if isinstance(a, dict) and a.get("address"):
            out.append({
                "address": str(a["address"]),
                "label": str(a.get("label") or f"wallet{len(out)+1}"),
            })
    return out


def _fetch_wallet_state(client: HLClient, address: str) -> tuple[dict, dict]:
    """One wallet -> (perp_state, spot_state). Errors return empty stubs."""
    try:
        perp = client.get_clearinghouse_state(address)
    except Exception as e:
        logger.warning("perp fetch failed for %s: %s", address[:10], e)
        perp = {"marginSummary": {"accountValue": "0"}, "assetPositions": []}
    try:
        spot = client.get_spot_clearinghouse_state(address)
    except Exception as e:
        logger.warning("spot fetch failed for %s: %s", address[:10], e)
        spot = {"balances": []}
    return perp, spot


def _build_portfolio(client: HLClient, accounts: list[dict]) -> Portfolio:
    raw: dict[str, dict] = {}
    for a in accounts:
        perp, spot = _fetch_wallet_state(client, a["address"])
        raw[a["label"]] = {"perp": perp, "spot": spot}
    return Portfolio.from_raw(raw, spot_resolver=client.resolve_spot_coin)


def _safe_fetch_marks() -> dict[str, float]:
    """Pull mark prices, never crash the bot if HL meta endpoint fails."""
    try:
        meta = fetch_meta_and_ctxs() or {}
    except Exception as e:
        logger.warning("marks fetch failed: %s", e)
        return {}
    return {sym: float(ctx.get("mark") or 0.0) for sym, ctx in meta.items() if ctx.get("mark")}


def _safe_oracai_current() -> Optional[dict]:
    try:
        return fetch_oracai_snapshot()
    except Exception as e:
        logger.warning("OracAI current snapshot failed: %s", e)
        return None


def _safe_oracai_yesterday(now: datetime) -> Optional[dict]:
    try:
        return fetch_snapshot_days_ago(1, now=now)
    except Exception as e:
        logger.warning("OracAI yesterday snapshot failed: %s", e)
        return None


def run_daily_monitor(
    whitelist_path: Path = DEFAULT_WHITELIST,
    decisions_path: Path = DEFAULT_DECISIONS,
    now: Optional[datetime] = None,
) -> None:
    now = now or datetime.now(timezone.utc)
    accounts = load_accounts(whitelist_path)
    if not accounts:
        send_messages(["⚠️ <b>daily-monitor</b>: нет аккаунтов в whitelist.yaml — нечего мониторить."])
        return

    client = HLClient()
    portfolio = _build_portfolio(client, accounts)

    decisions = load_decisions(decisions_path, lookback_days=DECISION_LOOKBACK_DAYS, now=now)
    matches = match_positions(portfolio.perp, decisions, now=now)

    today_snapshot = _safe_oracai_current()
    yesterday_snapshot = _safe_oracai_yesterday(now)

    marks = _safe_fetch_marks()

    alerts = evaluate_all(
        matches=matches,
        marks=marks,
        current_snapshot=today_snapshot,
        yesterday_snapshot=yesterday_snapshot,
        config=RuleConfig(),
    )

    messages = render_daily_report(
        matches=matches,
        alerts=alerts,
        marks=marks,
        current_snapshot=today_snapshot,
        total_account_value=portfolio.total_account_value,
        now=now,
        spot=portfolio.spot,
        wallet_count=len(accounts),
    )
    send_messages(messages)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        run_daily_monitor()
        return 0
    except Exception as e:
        tb = traceback.format_exc()
        logger.error("daily-monitor crashed: %s\n%s", e, tb)
        try:
            alert_owner(
                f"🛑 <b>daily-monitor crashed</b>\n<code>{type(e).__name__}: {e}</code>"
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
