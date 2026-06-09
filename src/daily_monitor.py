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
from src.hl_api import fetch_meta_and_ctxs, fetch_spot_meta_and_ctxs, fetch_candles
from src.hl_client import HLClient
from src.matcher import match_positions
from src.monitor_rules import evaluate_all, RuleConfig
from src.oracai import fetch_snapshot as fetch_oracai_snapshot
from src.oracai_history import fetch_snapshot_days_ago
from src.portfolio import Portfolio
from src.portfolio_performance import fetch_combined_performance
from src.sl_visibility import fetch_sl_orders_for_wallets
from src.ta import atr
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


def _safe_fetch_marks() -> tuple[dict[str, float], dict[str, float]]:
    """Pull mark + prev_day for both perp and spot tokens.

    Returns (marks, prev_day_marks). Spot tokens overwrite perp on collision —
    in practice they don't overlap (perp 'BTC' vs spot 'UBTC') so order is moot.
    Never crashes the bot if either endpoint fails.
    """
    marks: dict[str, float] = {}
    prev: dict[str, float] = {}

    try:
        perp_meta = fetch_meta_and_ctxs() or {}
        for sym, ctx in perp_meta.items():
            mk = ctx.get("mark")
            pd = ctx.get("prev_day")
            if mk:
                marks[sym] = float(mk)
            if pd:
                prev[sym] = float(pd)
    except Exception as e:
        logger.warning("perp marks fetch failed: %s", e)

    try:
        spot_meta = fetch_spot_meta_and_ctxs() or {}
        for sym, ctx in spot_meta.items():
            mk = ctx.get("mark")
            pd = ctx.get("prev_day")
            if mk and sym not in marks:
                marks[sym] = float(mk)
            if pd and sym not in prev:
                prev[sym] = float(pd)
    except Exception as e:
        logger.warning("spot marks fetch failed: %s", e)

    return marks, prev


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


def _run_digest_only(now: datetime, accounts: list[dict]) -> None:
    """Morning whitelist digest path when user has no positions.

    Builds the same Whitelist daily message that would normally appear at
    the bottom of the 10:00 MSK portfolio report, and sends it standalone.
    Also writes verdicts to the journal so the dataset keeps growing
    regardless of whether the user is in the market.
    """
    from pathlib import Path as _Path
    from src.whitelist_focus import (
        FOCUS_COINS, compute_all_verdicts, render_whitelist_verdicts,
    )
    from src.verdict_journal import VerdictEntry, append_verdicts

    repo_root = _Path(__file__).resolve().parent.parent
    state_dir = repo_root / "state"

    today_snapshot = _safe_oracai_current()
    marks, _ = _safe_fetch_marks()

    coin_data: dict[str, dict] = {}
    for c in FOCUS_COINS:
        try:
            candles = fetch_candles(c, interval="1d", lookback_days=220)
            closes = [float(k["c"]) for k in candles if k.get("c")] if candles else []
            coin_data[c] = {
                "mark": marks.get(c, 0.0),
                "candles_closes": closes if closes else None,
                "funding_apr_pct": None,
            }
        except Exception as e:
            logger.warning("Digest candles fetch failed for %s: %s", c, e)
            coin_data[c] = {"mark": marks.get(c, 0.0)}

    verdicts = compute_all_verdicts(
        now=now, coin_data=coin_data,
        regime_snapshot=today_snapshot, state_dir=state_dir,
    )
    digest = render_whitelist_verdicts(
        now=now, coin_data=coin_data,
        regime_snapshot=today_snapshot, state_dir=state_dir,
    )

    # Journal verdicts even when portfolio is empty — that's the whole
    # point of the journal: long-term dataset, independent of user state
    regime = (today_snapshot or {}).get("regime") if today_snapshot else None
    phase = (((today_snapshot or {}).get("cycle") or {}).get("phase")
             if today_snapshot else None)
    entries = [
        VerdictEntry(
            ts=now, source="whitelist_focus",
            coin=coin, mark=mark, verdict=verdict, rationale=rationale,
            regime=regime, phase=phase,
            verdict_raw=raw_v, rationale_raw=raw_r,
        )
        for coin, mark, verdict, rationale, raw_v, raw_r in verdicts
        if verdict != "NODATA"
    ]
    try:
        append_verdicts(state_dir / "verdict_journal.jsonl", entries)
        logger.info("Journaled %d verdicts (digest-only path)", len(entries))
    except Exception as e:
        logger.warning("Journal append failed: %s", e)

    try:
        send_messages([digest])
        logger.info("Digest-only message sent (%d chars)", len(digest))
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)


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

    has_perp = bool(portfolio.perp)
    has_spot = bool(portfolio.spot) and any(s.total > 0 for s in portfolio.spot)
    empty_portfolio = not has_perp and not has_spot

    # On the morning slot (10:00 MSK = 07:00 UTC), always run the whitelist
    # digest and journal it — even if user has no open positions. This is
    # the daily signal that needs to keep coming for the bot to be useful
    # AND for the verdict journal to keep accumulating data for future
    # backtesting. Empty-portfolio silence at other slots is correct.
    is_morning_slot = (now.hour == 7)

    if empty_portfolio and not is_morning_slot:
        logger.info("No positions and not morning slot — skipping report.")
        return

    if empty_portfolio:
        # Morning slot with no positions: send digest-only message + journal.
        # Skip the portfolio rendering path entirely.
        _run_digest_only(now, accounts)
        return

    decisions = load_decisions(decisions_path, lookback_days=DECISION_LOOKBACK_DAYS, now=now)
    matches = match_positions(portfolio.perp, decisions, now=now)

    today_snapshot = _safe_oracai_current()
    yesterday_snapshot = _safe_oracai_yesterday(now)

    marks, prev_day_marks = _safe_fetch_marks()

    # Phase 3.1: per-period PnL across all 3 wallets (one HL call per wallet)
    try:
        performance = fetch_combined_performance([a["address"] for a in accounts])
    except Exception as e:
        logger.warning("portfolio performance fetch failed: %s", e)
        performance = None

    # Phase 3.0.x: read user's hard SL orders from HL so we can show real
    # SL distance for orphan positions and alert on missing SL
    try:
        sl_orders = fetch_sl_orders_for_wallets(client, accounts)
    except Exception as e:
        logger.warning("SL orders fetch failed: %s", e)
        sl_orders = []

    # UI refinement round 2: compute ATR per orphan coin so the renderer
    # can show SL distance in volatility units ('0.4× ATR — likely intraday').
    # 30 D1 candles is enough for ATR(14); one call per orphan coin.
    coin_atrs: dict[str, float] = {}
    orphan_coins = {m.position.coin for m in matches if m.status == "orphan"}
    for coin in orphan_coins:
        try:
            candles = fetch_candles(coin, interval="1d", lookback_days=30)
            if candles and len(candles) >= 15:
                a = atr(candles, 14)
                if a is not None and a > 0:
                    coin_atrs[coin] = a
        except Exception as e:
            logger.warning("ATR fetch failed for %s: %s", coin, e)

    alerts = evaluate_all(
        matches=matches,
        marks=marks,
        current_snapshot=today_snapshot,
        yesterday_snapshot=yesterday_snapshot,
        config=RuleConfig(),
        sl_orders=sl_orders,
        coin_atrs=coin_atrs,
    )

    # Compute per-coin verdicts for positions the user currently holds
    # (fetched candles for ATR are reused — refetching D1 closes here
    # would just duplicate; instead fetch 220d candles per coin once).
    coin_verdicts: dict[str, str] = {}
    from src.whitelist_focus import evaluate_coin
    from pathlib import Path as _Path
    _repo_root = _Path(__file__).resolve().parent.parent
    _state_dir = _repo_root / "state"
    for coin in orphan_coins:
        try:
            candles = fetch_candles(coin, interval="1d", lookback_days=220)
            closes = [float(c["c"]) for c in candles if c.get("c")] if candles else []
            funding = None
            if marks.get(coin):
                # marks here is mark price; funding from meta context if present
                # (we don't have it here in the same form, skip — verdict will
                # work with TA + regime alone for the position-side display)
                pass
            v, _ = evaluate_coin(
                coin=coin, mark=marks.get(coin, 0.0),
                candles_closes=closes if closes else None,
                funding_apr_pct=funding,
                regime_snapshot=today_snapshot,
                state_dir=_state_dir, now=now,
            )
            if v in ("LONG", "SHORT", "WAIT"):
                coin_verdicts[coin] = v
        except Exception as e:
            logger.warning("Verdict computation failed for %s: %s", coin, e)

    # Morning digest: on the first run of the day (10:00 MSK = 07:00 UTC),
    # roll the whitelist daily verdicts into this portfolio message so the
    # user gets one consolidated morning ping instead of two.
    morning_digest: Optional[str] = None
    digest_verdicts: list = []  # for journal (only populated on morning run)
    if now.hour == 7:  # 07:00 UTC == 10:00 MSK first daily-monitor tick
        try:
            from src.whitelist_focus import (
                render_whitelist_verdicts, compute_all_verdicts, FOCUS_COINS,
            )
            digest_coin_data: dict[str, dict] = {}
            for c in FOCUS_COINS:
                try:
                    cs = fetch_candles(c, interval="1d", lookback_days=220)
                    closes_c = [float(k["c"]) for k in cs if k.get("c")] if cs else []
                    digest_coin_data[c] = {
                        "mark": marks.get(c, 0.0),
                        "candles_closes": closes_c if closes_c else None,
                        "funding_apr_pct": None,
                    }
                except Exception as e:
                    logger.warning("Digest candles failed for %s: %s", c, e)
                    digest_coin_data[c] = {"mark": marks.get(c, 0.0)}
            digest_verdicts = compute_all_verdicts(
                now=now, coin_data=digest_coin_data,
                regime_snapshot=today_snapshot, state_dir=_state_dir,
            )
            morning_digest = render_whitelist_verdicts(
                now=now, coin_data=digest_coin_data,
                regime_snapshot=today_snapshot, state_dir=_state_dir,
            )
        except Exception as e:
            logger.warning("Morning digest failed: %s", e)

    # Journal verdicts (both per-position and morning digest)
    try:
        from src.verdict_journal import VerdictEntry, append_verdicts
        regime = (today_snapshot or {}).get("regime") if today_snapshot else None
        phase = (((today_snapshot or {}).get("cycle") or {}).get("phase")
                 if today_snapshot else None)
        entries = []
        # Position verdicts — journaled every run so we see how the bot's
        # view on holdings evolves through the day
        for coin, verdict in coin_verdicts.items():
            entries.append(VerdictEntry(
                ts=now, source="daily_monitor",
                coin=coin, mark=marks.get(coin, 0.0),
                verdict=verdict, rationale="(position)",
                regime=regime, phase=phase,
            ))
        # Morning digest verdicts — journaled only on the morning run
        for coin, mark, verdict, rationale, raw_v, raw_r in digest_verdicts:
            if verdict != "NODATA":
                entries.append(VerdictEntry(
                    ts=now, source="whitelist_focus",
                    coin=coin, mark=mark, verdict=verdict, rationale=rationale,
                    regime=regime, phase=phase,
                    verdict_raw=raw_v, rationale_raw=raw_r,
                ))
        if entries:
            append_verdicts(_state_dir / "verdict_journal.jsonl", entries)
            logger.info("Journaled %d verdicts", len(entries))
    except Exception as e:
        logger.warning("Verdict journal append failed: %s", e)

    messages = render_daily_report(
        matches=matches,
        alerts=alerts,
        marks=marks,
        current_snapshot=today_snapshot,
        total_account_value=portfolio.total_account_value,
        now=now,
        spot=portfolio.spot,
        wallet_count=len(accounts),
        prev_day_marks=prev_day_marks,
        performance=performance,
        sl_orders=sl_orders,
        coin_atrs=coin_atrs,
        wallet_values=portfolio.wallet_values,
        coin_verdicts=coin_verdicts,
        morning_digest=morning_digest,
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
