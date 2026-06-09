"""Entry point for whitelist daily verdicts.

Runs every day at 09:00 MSK. Fetches mark + funding + 220d candles for
each of the 6 focus coins, computes per-coin LONG/SHORT/WAIT verdict
via whitelist_focus.evaluate_coin, sends one consolidated Telegram message.

Failure modes:
- HL meta fails -> exit 0 (logged), no report sent
- Single coin candles fail -> that coin uses 'нет данных', others go on
- OracAI fails -> verdicts computed without regime blocker
- Programmer error -> alert_owner + exit 1
"""
from __future__ import annotations

import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from src.hl_api import fetch_meta_and_ctxs, fetch_candles, resolve_symbol
from src.oracai import fetch_snapshot as fetch_oracai_snapshot
from src.telegram_sender import send_messages, alert_owner
from src.verdict_journal import VerdictEntry, append_verdicts
from src.whitelist_focus import (
    FOCUS_COINS, compute_all_verdicts, render_whitelist_verdicts,
)


logger = logging.getLogger("whitelist_focus_runner")


def _coin_data(coin: str, meta: dict) -> dict:
    """Fetch mark, prev_day, funding and candles for one coin."""
    sym = resolve_symbol(coin, meta) or coin
    ctx = meta.get(sym, {}) if isinstance(meta, dict) else {}
    mark = float(ctx.get("mark") or 0)
    funding = ctx.get("funding_apr_pct")

    candles_closes: list[float] = []
    if mark > 0:
        try:
            candles = fetch_candles(sym, interval="1d", lookback_days=220)
            if candles:
                candles_closes = [float(c["c"]) for c in candles if c.get("c")]
        except Exception as e:
            logger.warning("candles fetch failed for %s: %s", coin, e)

    return {
        "mark": mark,
        "candles_closes": candles_closes if candles_closes else None,
        "funding_apr_pct": float(funding) if funding is not None else None,
    }


def run() -> None:
    now = datetime.now(timezone.utc)
    repo_root = Path(__file__).resolve().parent.parent
    state_dir = repo_root / "state"

    try:
        meta = fetch_meta_and_ctxs() or {}
    except Exception as e:
        logger.warning("HL meta fetch failed: %s", e)
        return

    coin_data = {coin: _coin_data(coin, meta) for coin in FOCUS_COINS}

    try:
        oracai_snap = fetch_oracai_snapshot()
    except Exception as e:
        logger.warning("OracAI fetch failed: %s", e)
        oracai_snap = None

    # Compute verdicts once — same data feeds the journal and the message
    verdicts = compute_all_verdicts(
        now=now, coin_data=coin_data,
        regime_snapshot=oracai_snap, state_dir=state_dir,
    )

    # Journal: append every per-coin verdict so we can backtest later.
    # NODATA entries are skipped — no useful signal to evaluate.
    regime = (oracai_snap or {}).get("regime") if oracai_snap else None
    phase = (((oracai_snap or {}).get("cycle") or {}).get("phase")
             if oracai_snap else None)
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
    journal_path = state_dir / "verdict_journal.jsonl"
    try:
        written = append_verdicts(journal_path, entries)
        logger.info("Journaled %d verdicts to %s", written, journal_path)
    except Exception as e:
        logger.warning("Journal append failed: %s", e)

    msg = render_whitelist_verdicts(
        now=now,
        coin_data=coin_data,
        regime_snapshot=oracai_snap,
        state_dir=state_dir,
    )

    try:
        send_messages([msg])
        logger.info("Whitelist verdicts sent (%d chars)", len(msg))
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
            alert_owner(f"❌ whitelist_focus_runner упал:\n<pre>{tb[-2500:]}</pre>")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
