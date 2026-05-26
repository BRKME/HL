"""Entry point for signal_backtester.

Reads state/whale_signals.jsonl, fetches 1h candles per coin from HL for
the past N days, runs backtest, sends Telegram report.

Failure modes:
- No signals or empty file → emit short 'no data yet' message, exit 0
- HL candles fetch fails for one coin → skip that coin, continue
- Telegram fails → log warning, exit 0
"""
from __future__ import annotations

import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from src.hl_api import fetch_candles
from src.signal_backtester import (
    HORIZONS_HOURS,
    backtest_thresholds,
    load_signals,
    render_comparison_report,
)
from src.telegram_sender import alert_owner, send_messages


logger = logging.getLogger("signal_backtester_runner")

# Need enough history to cover the longest horizon (7d) plus signal span.
# Most signals are from past ~10 days so fetching 30d of 1h candles is plenty.
CANDLE_LOOKBACK_DAYS = 30


def _safe_fetch_candles(coin: str) -> list[dict]:
    try:
        return fetch_candles(coin, interval="1h",
                             lookback_days=CANDLE_LOOKBACK_DAYS) or []
    except Exception as e:
        logger.warning("candles fetch failed for %s: %s", coin, e)
        return []


def run() -> None:
    now = datetime.now(timezone.utc)
    repo_root = Path(__file__).resolve().parent.parent
    signals_path = repo_root / "state" / "whale_signals.jsonl"

    signals = load_signals(signals_path)
    if not signals:
        logger.info("no signals in %s — skipping run", signals_path)
        try:
            send_messages(["🎯 <b>Signal backtester</b>\n\n"
                            "Сигналов пока нет — ждём накопления."])
        except Exception as e:
            logger.warning("Telegram send failed: %s", e)
        return

    coins = sorted({s.coin for s in signals if s.coin and s.coin != "*"})
    logger.info("loaded %d signals across %d coins: %s",
                len(signals), len(coins), ", ".join(coins))

    candles_by_coin: dict[str, list[dict]] = {}
    for coin in coins:
        candles_by_coin[coin] = _safe_fetch_candles(coin)
        logger.info("  %s: %d candles", coin, len(candles_by_coin[coin]))

    # Run backtest at 3 notional thresholds to see how signal quality
    # changes with whale-size filtering — separates real moves from noise.
    results_by_threshold = backtest_thresholds(
        signals, candles_by_coin,
        thresholds=[0, 10_000, 50_000],
    )
    msg = render_comparison_report(results_by_threshold, now=now)

    try:
        send_messages([msg])
        logger.info("backtest report sent (%d chars)", len(msg))
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
            alert_owner(f"❌ signal_backtester упал:\n<pre>{tb[-2500:]}</pre>")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
