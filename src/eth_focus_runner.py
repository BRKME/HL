"""Entry point for ETH Saturday Focus runner.

Runs each Saturday 10:30 MSK. Fetches:
- ETH mark + prev_day from HL meta
- 220 days of D1 candles for TA
- ETH funding APR + OI from hl_ctx
- OracAI broad-market snapshot
- whale signals/fills from state/

Hands all of these to eth_focus.render_eth_focus, sends one Telegram message.

Failure modes:
- HL meta fails -> no report, log warning, exit 0 (not a crash)
- candles fail -> report goes out without TA section
- OracAI fails -> report goes out without regime section
- state/ missing or empty -> no whale section
- programmer error -> alert_owner + exit 1
"""
from __future__ import annotations

import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from src.eth_focus import compute_eth_verdict, render_eth_focus
from src.hl_api import fetch_meta_and_ctxs, fetch_candles, resolve_symbol
from src.oracai import fetch_snapshot as fetch_oracai_snapshot
from src.telegram_sender import send_messages, alert_owner
from src.verdict_journal import VerdictEntry, append_verdicts


logger = logging.getLogger("eth_focus_runner")

_FOCUS_COIN = "ETH"


def _safe_fetch_meta() -> tuple[dict, float, float, float, float]:
    """Return (meta, mark, prev_day, funding_apr_pct, open_interest_usd). Zeros on failure."""
    try:
        meta = fetch_meta_and_ctxs() or {}
    except Exception as e:
        logger.warning("HL meta fetch failed: %s", e)
        return {}, 0.0, 0.0, 0.0, 0.0

    sym = resolve_symbol(_FOCUS_COIN, meta) or _FOCUS_COIN
    ctx = meta.get(sym, {}) if isinstance(meta, dict) else {}
    mark = float(ctx.get("mark") or 0)
    prev_day = float(ctx.get("prev_day") or 0)
    funding = ctx.get("funding_apr_pct")
    # HL returns open_interest in contracts (not USD). Convert via mark.
    oi_contracts = ctx.get("open_interest")
    if oi_contracts is not None and mark > 0:
        oi_usd = float(oi_contracts) * mark
    else:
        oi_usd = 0.0

    return (
        meta,
        mark,
        prev_day,
        float(funding) if funding is not None else 0.0,
        oi_usd,
    )


def _safe_fetch_candles_closes(meta: dict) -> list[float]:
    """Return D1 closes for ETH over ~220 days. [] on any failure."""
    try:
        sym = resolve_symbol(_FOCUS_COIN, meta) or _FOCUS_COIN
        candles = fetch_candles(sym, interval="1d", lookback_days=220)
        if not candles:
            return []
        return [float(c["c"]) for c in candles if c.get("c")]
    except Exception as e:
        logger.warning("ETH candles fetch failed: %s", e)
        return []


def _safe_fetch_oracai() -> dict | None:
    try:
        return fetch_oracai_snapshot()
    except Exception as e:
        logger.warning("OracAI fetch failed: %s", e)
        return None


def run() -> None:
    now = datetime.now(timezone.utc)
    repo_root = Path(__file__).resolve().parent.parent
    state_dir = repo_root / "state"

    meta, mark, prev_day, funding, oi = _safe_fetch_meta()
    candles_closes = _safe_fetch_candles_closes(meta) if meta else []
    oracai_snap = _safe_fetch_oracai()

    msg = render_eth_focus(
        now=now,
        mark=mark,
        prev_day_mark=prev_day if prev_day > 0 else None,
        candles_closes=candles_closes or None,
        funding_apr_pct=funding if funding else None,
        open_interest_usd=oi if oi else None,
        regime_snapshot=oracai_snap,
        state_dir=state_dir,
    )

    if msg is None:
        logger.warning("ETH Focus report produced nothing (no mark). Skipping send.")
        return

    # Journal the verdict so we can backtest the model's effectiveness later
    from src.eth_focus import compute_eth_verdict_pair
    (raw_v, raw_r), (verdict, rationale) = compute_eth_verdict_pair(
        now=now, mark=mark,
        candles_closes=candles_closes or None,
        funding_apr_pct=funding if funding else None,
        regime_snapshot=oracai_snap,
        state_dir=state_dir,
    )
    if verdict != "NODATA":
        regime = (oracai_snap or {}).get("regime") if oracai_snap else None
        phase = (((oracai_snap or {}).get("cycle") or {}).get("phase")
                 if oracai_snap else None)
        try:
            append_verdicts(state_dir / "verdict_journal.jsonl", [VerdictEntry(
                ts=now, source="eth_focus",
                coin="ETH", mark=mark, verdict=verdict, rationale=rationale,
                regime=regime, phase=phase,
                verdict_raw=raw_v, rationale_raw=raw_r,
            )])
        except Exception as e:
            logger.warning("Journal append failed: %s", e)

    try:
        send_messages([msg])
        logger.info("ETH Focus report sent (%d chars)", len(msg))
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        run()
        return 0
    except Exception:
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        try:
            alert_owner(f"❌ eth_focus_runner упал:\n<pre>{tb[-2500:]}</pre>")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
