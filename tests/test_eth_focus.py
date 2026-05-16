"""Tests for src/eth_focus.py — Saturday ETH-specific weekly report.

Five sections, each optional (graceful degradation when data missing):
1. Header: mark, 24h, week, month moves
2. TA: RSI, EMA50/200, ATR, swing_low, trend description
3. Funding & OI: funding APR direction, OI USD
4. Whales (7d): net flow, cluster events, top whales
5. Market regime (OracAI, noted as broad)
6. Executive setup summary (descriptive, no entry levels)
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.eth_focus import (
    build_eth_focus_report,
    render_eth_focus,
    _section_header,
    _section_ta,
    _section_funding_oi,
    _section_whales,
    _section_regime,
    _section_setup,
    _read_recent_whale_signals,
    _read_recent_whale_fills,
)
from src.whale_correlation import (
    SIG_CLUSTER, SIG_OVERLAP, SIG_NEW_OPEN, SIG_FLIP,
    SEV_INFO, SEV_WARN, SEV_CRITICAL,
)


NOW = datetime(2026, 5, 23, 7, 30, tzinfo=timezone.utc)  # 10:30 MSK Saturday


def _candles(closes: list[float], days_back: int = 30) -> list[dict]:
    """Build a candle series ending NOW. Each close = (o,h,l,c) constant."""
    out = []
    start_ms = int((NOW - timedelta(days=days_back)).timestamp() * 1000)
    for i, c in enumerate(closes):
        ts = start_ms + i * 86_400_000
        out.append({"t": ts, "o": c, "h": c * 1.01, "l": c * 0.99, "c": c, "v": 1000})
    return out


# ---------- Header section ----------

def test_header_shows_mark_24h_week_month():
    closes = [2000.0] * 210 + [2100, 2150, 2180, 2160, 2170, 2175, 2175]  # 217 candles
    section = _section_header(
        mark=2175.0,
        prev_day=2253.0,
        candles=closes,
        now=NOW,
    )
    assert section is not None
    assert "2 175" in section or "2175" in section
    assert "24h" in section


def test_header_skips_when_no_mark():
    section = _section_header(mark=0.0, prev_day=0.0, candles=[2000.0]*210, now=NOW)
    assert section is None


# ---------- TA section ----------

def test_ta_section_includes_rsi_emas_atr():
    closes = [2000.0 + i * 2 for i in range(220)]  # uptrend
    section = _section_ta(closes, now=NOW)
    assert section is not None
    text = section.lower()
    assert "rsi" in text
    assert "ema" in text or "ema50" in text
    assert "atr" in text


def test_ta_section_describes_trend_above_both_emas():
    """Price > EMA50 > EMA200 → uptrend description."""
    closes = [2000.0 + i * 2 for i in range(220)]  # uptrend
    section = _section_ta(closes, now=NOW)
    # trend in any reasonable wording
    assert any(w in section.lower() for w in ("восходящ", "выше ema50", "тренд"))


def test_ta_section_describes_correction_pattern():
    """Above EMA200 but below EMA50 → correction in uptrend."""
    # Build: long uptrend, then dip in last ~10 candles
    closes = [2000.0 + i * 2 for i in range(210)] + [2400, 2380, 2360, 2340, 2300, 2280, 2280, 2280, 2280, 2280]
    section = _section_ta(closes, now=NOW)
    assert section is not None
    # accepts either Russian word for correction or descriptive phrase
    assert "коррекц" in section.lower() or "ниже EMA50" in section


def test_ta_section_skips_with_insufficient_candles():
    """Need >= 200 candles for EMA200; below that, gracefully skip."""
    section = _section_ta([2000.0] * 50, now=NOW)
    assert section is None


# ---------- Funding & OI section ----------

def test_funding_section_shows_positive_funding_pays_long():
    section = _section_funding_oi(funding_apr_pct=14.0, open_interest_usd=550_000_000)
    assert section is not None
    assert "+14" in section
    # describes who pays whom
    assert "long" in section.lower() or "лонг" in section.lower()


def test_funding_section_shows_negative_funding_pays_short():
    section = _section_funding_oi(funding_apr_pct=-8.0, open_interest_usd=400_000_000)
    assert section is not None
    assert "-8" in section
    assert "short" in section.lower() or "шорт" in section.lower()


def test_funding_section_shows_oi_in_compact_format():
    section = _section_funding_oi(funding_apr_pct=0.5, open_interest_usd=1_200_000_000)
    assert section is not None
    # $1.2B or similar compact format
    assert "1.2" in section or "1,2" in section
    assert "B" in section or "млрд" in section.lower()


def test_funding_section_skips_when_no_data():
    section = _section_funding_oi(funding_apr_pct=None, open_interest_usd=None)
    assert section is None


# ---------- Whale activity section ----------

def _whale_signal(rule, coin, days_ago, **details):
    ts = (NOW - timedelta(days=days_ago)).isoformat()
    return {
        "run_ts": ts, "rule": rule, "severity": 1, "coin": coin,
        "message": f"{coin} event", "details": details,
    }


def test_whales_section_counts_cluster_events_on_eth(tmp_path):
    signals_path = tmp_path / "whale_signals.jsonl"
    with signals_path.open("w") as fh:
        # 3 ETH clusters in past 7d, 1 BTC cluster (ignored)
        for i, days in enumerate([1, 3, 5]):
            fh.write(json.dumps(_whale_signal(SIG_CLUSTER, "ETH", days,
                whale_count=3, direction="long")) + "\n")
        fh.write(json.dumps(_whale_signal(SIG_CLUSTER, "BTC", 2,
            whale_count=3, direction="long")) + "\n")

    section = _section_whales(tmp_path, focus_coin="ETH", now=NOW)
    assert section is not None
    assert "3" in section  # 3 cluster events on ETH


def test_whales_section_skips_events_older_than_7d(tmp_path):
    signals_path = tmp_path / "whale_signals.jsonl"
    with signals_path.open("w") as fh:
        # 1 event 10 days ago (should be ignored), 1 event yesterday
        fh.write(json.dumps(_whale_signal(SIG_CLUSTER, "ETH", 10,
            whale_count=3, direction="long")) + "\n")
        fh.write(json.dumps(_whale_signal(SIG_CLUSTER, "ETH", 1,
            whale_count=2, direction="short")) + "\n")

    section = _section_whales(tmp_path, focus_coin="ETH", now=NOW)
    assert section is not None
    # only the recent one — "1 cluster event"
    assert "1" in section


def test_whales_section_separates_long_vs_short_clusters(tmp_path):
    signals_path = tmp_path / "whale_signals.jsonl"
    with signals_path.open("w") as fh:
        for _ in range(2):
            fh.write(json.dumps(_whale_signal(SIG_CLUSTER, "ETH", 2,
                whale_count=3, direction="long")) + "\n")
        fh.write(json.dumps(_whale_signal(SIG_CLUSTER, "ETH", 4,
            whale_count=2, direction="short")) + "\n")

    section = _section_whales(tmp_path, focus_coin="ETH", now=NOW)
    assert section is not None
    # mentions both directions
    assert "long" in section.lower() or "лонг" in section.lower()
    assert "short" in section.lower() or "шорт" in section.lower()


def test_whales_section_skips_when_signals_file_missing(tmp_path):
    section = _section_whales(tmp_path, focus_coin="ETH", now=NOW)
    # Gracefully None when no data
    assert section is None


def test_whales_section_includes_net_flow_from_fills(tmp_path):
    """If whale_fills.jsonl exists, compute net long/short notional on ETH."""
    fills_path = tmp_path / "whale_fills.jsonl"
    fills = [
        {"whale": "0xa", "coin": "ETH", "direction": "Open Long",
         "notional_usd": 500_000, "tid": 1,
         "time_ms": int((NOW - timedelta(days=1)).timestamp() * 1000),
         "side": "B", "size": 230, "price": 2175, "closed_pnl": 0,
         "crossed": True, "oid": 1},
        {"whale": "0xb", "coin": "ETH", "direction": "Open Long",
         "notional_usd": 300_000, "tid": 2,
         "time_ms": int((NOW - timedelta(days=2)).timestamp() * 1000),
         "side": "B", "size": 138, "price": 2175, "closed_pnl": 0,
         "crossed": True, "oid": 2},
        {"whale": "0xc", "coin": "ETH", "direction": "Open Short",
         "notional_usd": 200_000, "tid": 3,
         "time_ms": int((NOW - timedelta(days=3)).timestamp() * 1000),
         "side": "A", "size": 92, "price": 2175, "closed_pnl": 0,
         "crossed": True, "oid": 3},
    ]
    with fills_path.open("w") as fh:
        for f in fills:
            fh.write(json.dumps(f) + "\n")

    section = _section_whales(tmp_path, focus_coin="ETH", now=NOW)
    assert section is not None
    # net flow +600k (800 long - 200 short) — mentioned in some form
    text = section.lower()
    assert "net" in text or "поток" in text or "флоу" in text or "+" in section


# ---------- Regime section ----------

def test_regime_section_renders_oracai_snapshot_with_caveat():
    snapshot = {"regime": "BULL", "cycle": {"phase": "MID_BULL"}}
    section = _section_regime(snapshot)
    assert section is not None
    assert "BULL" in section
    assert "MID_BULL" in section
    # honest caveat that this is broad market, not ETH-specific
    assert "broad" in section.lower() or "общ" in section.lower() or "не специф" in section.lower()


def test_regime_section_skips_when_no_snapshot():
    assert _section_regime(None) is None


# ---------- Setup summary ----------

def test_setup_summary_describes_state_without_entry_levels():
    """Per phase 3.4 decision: descriptive only, no prescriptive entry levels."""
    ta_data = {
        "rsi_d1": 45,
        "above_ema50": False,
        "above_ema200": True,
        "vs_ema50_pct": -3.0,
        "vs_ema200_pct": 13.0,
    }
    section = _section_setup(
        ta=ta_data,
        funding_apr_pct=14.0,
        whale_cluster_count=3,
        whale_net_long=True,
        regime="BULL",
    )
    assert section is not None
    text = section.lower()
    # No "entry $X", no "SL $Y", no "buy at"
    forbidden = ["entry $", "купить @", "buy at", "вход $"]
    for f in forbidden:
        assert f not in text
    # But descriptive bits should be present
    assert "ema200" in text or "тренд" in text or "коррекц" in text


def test_setup_summary_skips_when_no_data():
    section = _section_setup(ta=None, funding_apr_pct=None,
                              whale_cluster_count=0, whale_net_long=None,
                              regime=None)
    assert section is None


# ---------- end-to-end render ----------

def test_render_eth_focus_combines_all_sections(tmp_path):
    """Smoke: build report with all data present, see sections show up."""
    # whale_signals
    signals_path = tmp_path / "whale_signals.jsonl"
    with signals_path.open("w") as fh:
        for _ in range(2):
            fh.write(json.dumps(_whale_signal(SIG_CLUSTER, "ETH", 2,
                whale_count=3, direction="long")) + "\n")

    msg = render_eth_focus(
        now=NOW,
        mark=2175.0,
        prev_day_mark=2253.0,
        candles_closes=[2000.0 + i * 2 for i in range(220)],
        funding_apr_pct=14.0,
        open_interest_usd=550_000_000,
        regime_snapshot={"regime": "BULL", "cycle": {"phase": "MID_BULL"}},
        state_dir=tmp_path,
    )
    assert msg is not None
    assert "ETH Saturday Focus" in msg or "ETH" in msg
    assert "RSI" in msg or "rsi" in msg.lower()
    assert "14" in msg  # funding
    assert "BULL" in msg


def test_render_eth_focus_with_missing_data_returns_minimal_report(tmp_path):
    """Even if everything except mark fails, we still produce a header."""
    msg = render_eth_focus(
        now=NOW,
        mark=2175.0,
        prev_day_mark=None,
        candles_closes=None,
        funding_apr_pct=None,
        open_interest_usd=None,
        regime_snapshot=None,
        state_dir=tmp_path,
    )
    assert msg is not None
    assert "ETH" in msg


def test_render_eth_focus_returns_none_when_no_mark(tmp_path):
    """No price = no useful report."""
    msg = render_eth_focus(
        now=NOW, mark=0.0, prev_day_mark=None, candles_closes=None,
        funding_apr_pct=None, open_interest_usd=None,
        regime_snapshot=None, state_dir=tmp_path,
    )
    assert msg is None


def test_render_eth_focus_under_telegram_limit(tmp_path):
    """Output must fit one Telegram message (4096 chars)."""
    msg = render_eth_focus(
        now=NOW,
        mark=2175.0,
        prev_day_mark=2253.0,
        candles_closes=[2000.0 + (i % 30) for i in range(220)],
        funding_apr_pct=14.0,
        open_interest_usd=550_000_000,
        regime_snapshot={"regime": "BULL", "cycle": {"phase": "MID_BULL"}},
        state_dir=tmp_path,
    )
    assert msg is not None
    assert len(msg) <= 4096


# ---------- Whale reader helpers ----------

def test_read_recent_whale_signals_filters_by_age_and_coin(tmp_path):
    path = tmp_path / "whale_signals.jsonl"
    with path.open("w") as fh:
        fh.write(json.dumps(_whale_signal(SIG_CLUSTER, "ETH", 2,
            whale_count=3, direction="long")) + "\n")
        fh.write(json.dumps(_whale_signal(SIG_CLUSTER, "ETH", 10,
            whale_count=3, direction="long")) + "\n")  # too old
        fh.write(json.dumps(_whale_signal(SIG_CLUSTER, "BTC", 1,
            whale_count=3, direction="long")) + "\n")  # wrong coin
    rows = _read_recent_whale_signals(tmp_path, coin="ETH", days=7, now=NOW)
    assert len(rows) == 1


def test_read_recent_whale_signals_missing_file_returns_empty(tmp_path):
    rows = _read_recent_whale_signals(tmp_path, coin="ETH", days=7, now=NOW)
    assert rows == []


def test_read_recent_whale_fills_filters_by_age(tmp_path):
    path = tmp_path / "whale_fills.jsonl"
    with path.open("w") as fh:
        fh.write(json.dumps({
            "whale": "0xa", "coin": "ETH", "direction": "Open Long",
            "notional_usd": 500_000, "tid": 1,
            "time_ms": int((NOW - timedelta(days=1)).timestamp() * 1000),
            "side": "B", "size": 230, "price": 2175, "closed_pnl": 0,
            "crossed": True, "oid": 1,
        }) + "\n")
        fh.write(json.dumps({
            "whale": "0xb", "coin": "ETH", "direction": "Open Long",
            "notional_usd": 100_000, "tid": 2,
            "time_ms": int((NOW - timedelta(days=20)).timestamp() * 1000),  # too old
            "side": "B", "size": 46, "price": 2175, "closed_pnl": 0,
            "crossed": True, "oid": 2,
        }) + "\n")
    rows = _read_recent_whale_fills(tmp_path, coin="ETH", days=7, now=NOW)
    assert len(rows) == 1
