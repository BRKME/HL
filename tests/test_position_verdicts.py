"""Tests for position verdicts and morning whitelist digest in daily monitor."""
from datetime import datetime, timezone

from src.daily_report import render_daily_report
from src.matcher import MatchResult
from src.portfolio import AggregatedPerpPosition
from src.sl_visibility import SLOrder


NOW = datetime(2026, 6, 3, 7, 0, tzinfo=timezone.utc)  # 10:00 MSK morning slot


def _pos(coin, size=1.0, entry=2000):
    return AggregatedPerpPosition(
        coin=coin, net_size=size, weighted_entry=entry, total_pnl=0,
        contributors=[("1", size)], avg_leverage=3, max_liquidation_distance_pct=0,
    )


def _orphan(pos):
    return MatchResult(position=pos, decision=None, status="orphan")


# ---------- Position-side verdict markers ----------

def test_position_shows_long_verdict_when_aligned():
    """LONG position + bot says LONG → 🟢 LONG, no warning."""
    eth = _pos("ETH", size=1.0, entry=2000)
    msgs = render_daily_report(
        matches=[_orphan(eth)], alerts=[], marks={"ETH": 2000.0},
        current_snapshot=None, total_account_value=2000, now=NOW,
        coin_verdicts={"ETH": "LONG"},
    )
    text = "\n".join(msgs)
    eth_line = next(l for l in text.split("\n") if "ETH" in l and "LONG" in l[:30])
    assert "🟢 LONG" in eth_line
    # No mismatch warning
    assert "⚠️" not in eth_line


def test_position_shows_mismatch_when_bot_says_opposite():
    """LONG position + bot says SHORT → ⚠️ mismatch."""
    eth = _pos("ETH", size=1.0, entry=2000)
    msgs = render_daily_report(
        matches=[_orphan(eth)], alerts=[], marks={"ETH": 2000.0},
        current_snapshot=None, total_account_value=2000, now=NOW,
        coin_verdicts={"ETH": "SHORT"},
    )
    text = "\n".join(msgs)
    eth_line = next(l for l in text.split("\n") if "ETH" in l and "LONG" in l[:30])
    assert "🔴 SHORT" in eth_line
    assert "⚠️" in eth_line


def test_position_shows_wait_verdict_no_mismatch_warning():
    """LONG + bot says WAIT — no mismatch (just neutral verdict)."""
    eth = _pos("ETH", size=1.0, entry=2000)
    msgs = render_daily_report(
        matches=[_orphan(eth)], alerts=[], marks={"ETH": 2000.0},
        current_snapshot=None, total_account_value=2000, now=NOW,
        coin_verdicts={"ETH": "WAIT"},
    )
    text = "\n".join(msgs)
    eth_line = next(l for l in text.split("\n") if "ETH" in l and "LONG" in l[:30])
    assert "⚪ WAIT" in eth_line
    # WAIT is not a mismatch
    assert "⚠️" not in eth_line


def test_position_no_verdict_marker_when_dict_empty():
    """Backward compat: no coin_verdicts → no marker appended."""
    eth = _pos("ETH", size=1.0, entry=2000)
    msgs = render_daily_report(
        matches=[_orphan(eth)], alerts=[], marks={"ETH": 2000.0},
        current_snapshot=None, total_account_value=2000, now=NOW,
    )
    text = "\n".join(msgs)
    eth_line = next(l for l in text.split("\n") if "ETH" in l and "LONG" in l[:30])
    assert "🟢" not in eth_line
    assert "🔴 SHORT" not in eth_line
    assert "⚪" not in eth_line


# ---------- Morning digest in daily report ----------

def test_morning_digest_appears_at_end_when_provided():
    """morning_digest text is appended at the bottom of the report."""
    digest = "🎯 Whitelist daily test digest\n🟢 BTC $80000 — ВХОДИТЬ LONG"
    msgs = render_daily_report(
        matches=[], alerts=[], marks={},
        current_snapshot=None, total_account_value=2000, now=NOW,
        morning_digest=digest,
    )
    text = "\n".join(msgs)
    assert "Whitelist daily test digest" in text
    assert "🟢 BTC" in text


def test_no_morning_digest_when_not_provided():
    msgs = render_daily_report(
        matches=[], alerts=[], marks={},
        current_snapshot=None, total_account_value=2000, now=NOW,
    )
    text = "\n".join(msgs)
    assert "Whitelist daily" not in text


def test_short_position_long_verdict_is_mismatch():
    """SHORT position + bot says LONG → ⚠️."""
    eth = _pos("ETH", size=-1.0, entry=2000)
    msgs = render_daily_report(
        matches=[_orphan(eth)], alerts=[], marks={"ETH": 2000.0},
        current_snapshot=None, total_account_value=2000, now=NOW,
        coin_verdicts={"ETH": "LONG"},
    )
    text = "\n".join(msgs)
    # the position row contains 'SHORT' from side rendering
    eth_line = next(l for l in text.split("\n") if "ETH" in l and "SHORT" in l[:30])
    assert "⚠️" in eth_line
