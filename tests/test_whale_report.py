"""Tests for src/whale_report.py — Telegram rendering of whale signals."""
from datetime import datetime, timedelta, timezone

import pytest

from src.whale_correlation import (
    Signal, SIG_CLUSTER, SIG_OVERLAP, SIG_NEW_OPEN, SIG_FLIP,
    SEV_INFO, SEV_WARN,
)
from src.whale_report import (
    render_instant_alerts,
    render_digest,
    split_by_mode,
)


NOW = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)


def _sig(rule, severity, coin, whale="0x111aaaa222222222222222222222222222222222", **details):
    if "whale" not in details and rule in (SIG_OVERLAP, SIG_NEW_OPEN, SIG_FLIP):
        details["whale"] = whale
    return Signal(rule=rule, severity=severity, coin=coin,
                  message=f"{coin} alert", details=details)


# ---------- split_by_mode ----------

def test_split_by_mode_separates_warn_from_info():
    warn = _sig(SIG_CLUSTER, SEV_WARN, "ETH", whale_count=3, direction="long")
    info = _sig(SIG_OVERLAP, SEV_INFO, "BTC", whale_side="long",
                user_side="long", winrate_used=0.65, notional_usd=120000)
    instant, digest = split_by_mode([warn, info])
    assert instant == [warn]
    assert digest == [info]


def test_split_by_mode_empty():
    assert split_by_mode([]) == ([], [])


def test_split_by_mode_critical_goes_to_instant():
    """Future-proof: critical (3) also instant."""
    crit = _sig(SIG_FLIP, 3, "BTC", from_side="long", to_side="short",
                whale="0xabc111111111111111111111111111111111aaaa", notional_usd=300000)
    instant, digest = split_by_mode([crit])
    assert instant == [crit]
    assert digest == []


# ---------- render_instant_alerts (warn only) ----------

def test_render_instant_empty_returns_none():
    assert render_instant_alerts([], now=NOW) is None


def test_render_instant_cluster_message_has_required_pieces():
    s = _sig(SIG_CLUSTER, SEV_WARN, "ETH",
             whale_count=3, direction="long",
             whales=["0xa", "0xb", "0xc"])
    msg = render_instant_alerts([s], now=NOW)
    assert msg is not None
    # marker
    assert "🐋" in msg
    # cluster + coin + direction + whale count
    assert "ETH" in msg
    assert "LONG" in msg
    assert "3" in msg


def test_render_instant_flip_shows_direction_change():
    s = _sig(SIG_FLIP, SEV_WARN, "BTC",
             whale="0xabcdef1111111111111111111111111111111111",
             from_side="long", to_side="short", notional_usd=250_000)
    msg = render_instant_alerts([s], now=NOW)
    assert "BTC" in msg
    assert "LONG" in msg and "SHORT" in msg
    # short whale id
    assert "0xabcdef" in msg


def test_render_instant_multiple_signals_one_message():
    """All instant alerts ride in one message — not 4 separate ones."""
    sigs = [
        _sig(SIG_CLUSTER, SEV_WARN, "ETH", whale_count=3, direction="long"),
        _sig(SIG_FLIP, SEV_WARN, "BTC",
             whale="0xaaa111111111111111111111111111111111aaaa",
             from_side="long", to_side="short", notional_usd=200_000),
    ]
    msg = render_instant_alerts(sigs, now=NOW)
    assert "ETH" in msg and "BTC" in msg


def test_render_instant_header_has_msk_time():
    s = _sig(SIG_CLUSTER, SEV_WARN, "ETH", whale_count=3, direction="long")
    msg = render_instant_alerts([s], now=NOW)
    # NOW = 12:00 UTC → 15:00 MSK
    assert "15:00" in msg


def test_render_instant_under_telegram_limit():
    """20 cluster signals should still fit under 4096 char limit."""
    sigs = [_sig(SIG_CLUSTER, SEV_WARN, f"COIN{i}", whale_count=3, direction="long")
            for i in range(20)]
    msg = render_instant_alerts(sigs, now=NOW)
    assert len(msg) <= 4096


def test_render_instant_escapes_html():
    s = Signal(rule=SIG_CLUSTER, severity=SEV_WARN, coin="<bad>",
               message="x", details={"whale_count": 3, "direction": "long"})
    msg = render_instant_alerts([s], now=NOW)
    assert "<bad>" not in msg
    assert "&lt;bad&gt;" in msg


# ---------- render_digest (info, daily) ----------

def test_render_digest_empty_returns_none():
    assert render_digest([], now=NOW) is None


def test_render_digest_groups_by_rule():
    sigs = [
        _sig(SIG_OVERLAP, SEV_INFO, "BTC", whale_side="long", user_side="long",
             winrate_used=0.65, notional_usd=120_000),
        _sig(SIG_OVERLAP, SEV_INFO, "ETH", whale_side="long", user_side="long",
             winrate_used=0.70, notional_usd=200_000),
        _sig(SIG_NEW_OPEN, SEV_INFO, "SOL", direction="long",
             notional_usd=180_000, winrate_used=0.68),
    ]
    msg = render_digest(sigs, now=NOW)
    # both rules present as section headers
    assert "OVERLAP" in msg.upper() or "Совпадения" in msg
    assert "NEW" in msg.upper() or "Новые" in msg or "входы" in msg.lower()


def test_render_digest_dedups_repeat_signals_to_count():
    """Same (rule, coin, whale) appearing multiple times shows as 'x N'."""
    whale = "0xabc111111111111111111111111111111111aaaa"
    sigs = [
        _sig(SIG_OVERLAP, SEV_INFO, "BTC", whale=whale, whale_side="long",
             user_side="long", winrate_used=0.65, notional_usd=120_000),
        _sig(SIG_OVERLAP, SEV_INFO, "BTC", whale=whale, whale_side="long",
             user_side="long", winrate_used=0.65, notional_usd=140_000),
        _sig(SIG_OVERLAP, SEV_INFO, "BTC", whale=whale, whale_side="long",
             user_side="long", winrate_used=0.65, notional_usd=180_000),
    ]
    msg = render_digest(sigs, now=NOW)
    # one line for BTC, mentions 3
    assert msg.count("BTC") == 1
    assert "×3" in msg or "x3" in msg or "(3)" in msg


def test_render_digest_includes_window_label():
    """Header says 'за 24ч' or similar so user knows the lookback."""
    s = _sig(SIG_OVERLAP, SEV_INFO, "BTC", whale_side="long", user_side="long",
             winrate_used=0.65, notional_usd=100_000)
    msg = render_digest([s], now=NOW)
    assert "24" in msg or "сутк" in msg.lower()


def test_render_digest_under_telegram_limit():
    sigs = [_sig(SIG_NEW_OPEN, SEV_INFO, f"COIN{i}", direction="long",
                 notional_usd=150_000, winrate_used=0.6,
                 whale=f"0x{i:040d}") for i in range(80)]
    msg = render_digest(sigs, now=NOW)
    assert len(msg) <= 4096


def test_render_digest_sorts_by_count_descending():
    """Most-frequent signals first in each section."""
    whale = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    sigs = (
        # BTC appears twice
        [_sig(SIG_NEW_OPEN, SEV_INFO, "BTC", whale=whale, direction="long",
              notional_usd=100_000, winrate_used=0.60) for _ in range(2)]
        # ETH appears 5 times — should rank first
        + [_sig(SIG_NEW_OPEN, SEV_INFO, "ETH", whale=whale, direction="long",
                notional_usd=100_000, winrate_used=0.60) for _ in range(5)]
    )
    msg = render_digest(sigs, now=NOW)
    eth_pos = msg.find("ETH")
    btc_pos = msg.find("BTC")
    assert eth_pos != -1 and btc_pos != -1
    assert eth_pos < btc_pos


# ---------- rank-events section ----------

def test_digest_new_entrant_only_is_none():
    # rank-события убраны из канала: один NEW_ENTRANT -> нечего слать
    sigs = [Signal(
        rule="WHALE_NEW_ENTRANT", severity=SEV_INFO, coin="*",
        message="новый кит",
        details={"whale": "0xabc123def456abc123def456abc123def456abcd",
                 "last_rank": 23, "consecutive_in_top": 3, "runs_in_top": 3},
    )]
    assert render_digest(sigs, now=NOW) is None


def test_digest_drop_off_only_is_none():
    sigs = [Signal(
        rule="WHALE_DROP_OFF", severity=SEV_INFO, coin="*",
        message="кит ушёл",
        details={"whale": "0xdef789abc123def789abc123def789abc123def7",
                 "last_rank": 47, "runs_in_top": 25, "consecutive_in_top": 0},
    )]
    assert render_digest(sigs, now=NOW) is None


def test_digest_keeps_new_open_drops_rank_event():
    sigs = [
        Signal(rule=SIG_NEW_OPEN, severity=SEV_INFO, coin="ETH",
               message="x", details={"whale": "0xa", "direction": "long",
                                     "notional_usd": 200_000, "winrate_used": 0.65}),
        Signal(rule="WHALE_NEW_ENTRANT", severity=SEV_INFO, coin="*",
               message="y", details={"whale": "0xb111aaaa222222222222222222222222222222aa",
                                     "last_rank": 15, "consecutive_in_top": 4, "runs_in_top": 4}),
    ]
    msg = render_digest(sigs, now=NOW)
    assert "ETH" in msg            # полезный NEW_OPEN остаётся
    assert "вошёл в топ" not in msg     # rank-событие вырезано
    assert "Изменения в топе" not in msg


# ---------- rank-секция убрана из дайджеста (UX-фидбек 12.06) ----------

def test_digest_omits_rank_churn():
    """Вошёл/ушёл из топа — ротация лидерборда, не для канала."""
    from src.whale_report import Signal
    sigs = [
        Signal(rule="WHALE_NEW_ENTRANT", severity=0, coin="", message="",
               details={"whale": "0xabc", "last_rank": 44, "consecutive_in_top": 3}),
        Signal(rule="WHALE_DROP_OFF", severity=0, coin="", message="",
               details={"whale": "0xdef", "runs_in_top": 90, "last_rank": 50}),
    ]
    msg = render_digest(sigs, now=NOW)
    # только rank-сигналы -> дайджест пустой (None), не шлётся
    assert msg is None


def test_digest_keeps_actionable_drops_rank():
    """Полезные overlap/new_open остаются, rank-шум вырезан из смешанного."""
    from src.whale_report import Signal
    sigs = [
        Signal(rule="WHALE_OVERLAP", severity=1, coin="BTC", message="3 кита в BTC",
               details={"coin": "BTC", "whale_side": "long", "count": 3}),
        Signal(rule="WHALE_NEW_ENTRANT", severity=0, coin="", message="",
               details={"whale": "0xabc", "last_rank": 44, "consecutive_in_top": 3}),
    ]
    msg = render_digest(sigs, now=NOW)
    assert msg is not None
    assert "топ" not in msg.lower() or "вошёл" not in msg
    assert "Изменения в топе" not in msg
