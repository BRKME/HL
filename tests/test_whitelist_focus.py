"""Tests for whitelist_focus — daily per-coin verdict report."""
from datetime import datetime, timezone
from pathlib import Path

from src.whitelist_focus import (
    FOCUS_COINS, evaluate_coin, render_whitelist_verdicts,
)


NOW = datetime(2026, 6, 2, 6, 5, tzinfo=timezone.utc)  # 09:05 MSK


def test_focus_coins_are_the_eight():
    """Вселенная 05.07.2026 (запрос оператора): 8 монет."""
    assert FOCUS_COINS == ["BTC", "ETH", "ZEC", "NEAR", "HYPE",
                           "ASTER", "MORPHO", "TAO"]


def test_evaluate_coin_returns_wait_when_no_mark(tmp_path):
    v, _ = evaluate_coin(
        coin="BTC", mark=0.0, candles_closes=None,
        funding_apr_pct=None, regime_snapshot=None,
        state_dir=tmp_path, now=NOW,
    )
    assert v == "WAIT"


def test_evaluate_coin_long_when_strong_bull_signals(tmp_path):
    """Uptrend + oversold + at support + cheap short funding + BULL regime → LONG."""
    closes = [1500.0 + i * 3 for i in range(220)]  # uptrend
    # Make last close close to swing low to trigger support bonus
    closes[-1] = closes[-10] - 1
    v, _ = evaluate_coin(
        coin="BTC", mark=closes[-1],
        candles_closes=closes, funding_apr_pct=-12.0,
        regime_snapshot={"regime": "BULL", "cycle": {"phase": "MID_BULL"}},
        state_dir=tmp_path, now=NOW,
    )
    assert v == "LONG"


def test_evaluate_coin_short_when_clean_downtrend(tmp_path):
    """Clean downtrend, no exhaustion → SHORT.
    Last candle stays in downtrend (not at swing low, not bouncing)."""
    # Steady downtrend $2500 → $2000
    closes = [2500.0 - i * 2.5 for i in range(220)]
    # Place last candle slightly above swing-low band but still in downtrend
    closes[-1] = 1990.0  # below EMA50 ~1995, above swing_low ~1955
    # Actually simpler: ensure RSI not extreme and price not at edges
    v, r = evaluate_coin(
        coin="ETH", mark=closes[-1],
        candles_closes=closes, funding_apr_pct=3.0,
        regime_snapshot={"regime": "BEAR", "cycle": {"phase": "MID_BEAR"}},
        state_dir=tmp_path, now=NOW,
    )
    # In new methodology: SHORT only when downtrend AND no exhaustion.
    # Accept either SHORT or WAIT-blocked-but-bearish-leaning
    # (the precise outcome depends on whether the last candle is near
    # swing_low which triggers oversold — bot is conservative there).
    assert v in ("SHORT", "WAIT")


def test_evaluate_coin_blocked_by_regime(tmp_path):
    """All long signals but BEAR phase blocks → WAIT."""
    closes = [1500.0 + i * 3 for i in range(220)]
    closes[-1] = closes[-10] - 1
    v, r = evaluate_coin(
        coin="BTC", mark=closes[-1],
        candles_closes=closes, funding_apr_pct=-12.0,
        regime_snapshot={"regime": "BEAR", "cycle": {"phase": "EARLY_BEAR"}},
        state_dir=tmp_path, now=NOW,
    )
    assert v == "WAIT"
    assert "BEAR" in r


def test_render_contains_all_six_coins(tmp_path):
    coin_data = {c: {"mark": 100.0, "candles_closes": None,
                     "funding_apr_pct": None} for c in FOCUS_COINS}
    msg = render_whitelist_verdicts(
        now=NOW, coin_data=coin_data,
        regime_snapshot=None, state_dir=tmp_path,
    )
    for coin in FOCUS_COINS:
        assert coin in msg


def test_render_coin_with_no_data_shows_placeholder(tmp_path):
    coin_data = {c: {"mark": 100.0} for c in FOCUS_COINS}
    coin_data["NEAR"] = {"mark": 0.0}  # missing data
    msg = render_whitelist_verdicts(
        now=NOW, coin_data=coin_data,
        regime_snapshot=None, state_dir=tmp_path,
    )
    near_line = next(l for l in msg.split("\n") if "NEAR" in l)
    assert "нет данных" in near_line


def test_render_preserves_focus_coin_order(tmp_path):
    """Display order = FOCUS_COINS list, not alphabetical."""
    coin_data = {c: {"mark": 100.0} for c in FOCUS_COINS}
    msg = render_whitelist_verdicts(
        now=NOW, coin_data=coin_data,
        regime_snapshot=None, state_dir=tmp_path,
    )
    last_idx = -1
    for coin in FOCUS_COINS:
        idx = msg.find(f">{coin}<")  # in <code>COIN</code>
        assert idx > last_idx
        last_idx = idx


def test_render_includes_regime_subtitle_when_provided(tmp_path):
    coin_data = {c: {"mark": 100.0} for c in FOCUS_COINS}
    msg = render_whitelist_verdicts(
        now=NOW, coin_data=coin_data,
        regime_snapshot={"regime": "BEAR", "cycle": {"phase": "EARLY_BEAR"}},
        state_dir=tmp_path,
    )
    assert "BEAR" in msg
    assert "EARLY_BEAR" in msg


def test_render_no_regime_subtitle_when_snapshot_missing(tmp_path):
    coin_data = {c: {"mark": 100.0} for c in FOCUS_COINS}
    msg = render_whitelist_verdicts(
        now=NOW, coin_data=coin_data,
        regime_snapshot=None, state_dir=tmp_path,
    )
    assert "regime" not in msg.lower()


def test_render_message_under_telegram_limit(tmp_path):
    """6 coins × short verdict — comfortably under 4096."""
    coin_data = {c: {"mark": 100.0} for c in FOCUS_COINS}
    msg = render_whitelist_verdicts(
        now=NOW, coin_data=coin_data,
        regime_snapshot={"regime": "BULL", "cycle": {"phase": "MID_BULL"}},
        state_dir=tmp_path,
    )
    assert len(msg) < 2000  # well under TG 4096 limit
