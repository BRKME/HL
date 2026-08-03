"""Header exposure + stale-exit age (03.08.2026).

Header showed «• $62» (equity) directly above positions worth $164 and $143.
Both numbers are correct but the pair reads as a contradiction unless the
notional and the resulting leverage are stated. The leverage badge had been
dropped in UI round 3 on the assumption «user always runs at 2×» — that
assumption no longer holds (~5× on 03.08).

Pending exits also carried no age: the NEAR requirement had been repeating
since 21.07 with nothing in the line to say so.
"""
from datetime import datetime, timedelta, timezone

from src.daily_report import _render_header, _render_orphan
from src.decisions_log import Decision
from src.matcher import MatchResult
from src.portfolio import AggregatedPerpPosition

NOW = datetime(2026, 8, 3, 17, 8, tzinfo=timezone.utc)


def _pos(coin, net_size, entry):
    return AggregatedPerpPosition(
        coin=coin, net_size=net_size, weighted_entry=entry, total_pnl=10.0,
        contributors=[("main", net_size)], avg_leverage=5.0,
        max_liquidation_distance_pct=25.0,
    )


def _dec(coin, entry):
    return Decision(
        ts=NOW - timedelta(days=5), signal="MODERATE", coin=coin, entry=entry,
        alloc_usd=150.0, expected_size=150.0 / entry, sl_price=entry * 0.93,
        sl_pct=-7.0, sl_method="atr", atr14=entry * 0.03,
        regime_at_entry="BULL", phase_at_entry="MID_BULL",
    )


def _matches():
    # $164 NEAR + $143 ZEC on $62 equity ~= 4.95x
    return [
        MatchResult(position=_pos("NEAR", 100.0, 1.64),
                    decision=None, status="orphan"),
        MatchResult(position=_pos("ZEC", 10.0, 14.3),
                    decision=None, status="orphan"),
    ]


MARKS = {"NEAR": 1.64, "ZEC": 14.3}


# ------------------------------------------------------------------- header

def test_header_shows_notional_and_leverage():
    out = _render_header(NOW, 62.0, 3, matches=_matches(), marks=MARKS)
    assert "$62" in out           # equity still first
    assert "307" in out           # notional
    assert "5.0×" in out or "4.9×" in out


def test_header_without_positions_has_no_leverage():
    out = _render_header(NOW, 62.0, 3, matches=[], marks={})
    assert "×" not in out
    assert "$62" in out


def test_header_no_leverage_when_equity_unknown():
    out = _render_header(NOW, None, 3, matches=_matches(), marks=MARKS)
    assert "×" not in out


# ------------------------------------------------------- pending exit age

def _pend(days_ago, reason="verdict_flip"):
    ts = (NOW - timedelta(days=days_ago)).isoformat()
    return {"NEAR": {"ts": ts, "exit_reason": reason,
                     "closed_direction": "LONG"}}


def test_pending_exit_shows_age_in_days():
    out = _render_orphan(_matches(), MARKS, pending_exit=_pend(13), now=NOW)
    assert "ЗАКРОЙ" in out
    assert "13 дн" in out


def test_fresh_pending_exit_shows_hours():
    out = _render_orphan(_matches(), MARKS,
                         pending_exit=_pend(0.25), now=NOW)
    assert "ЗАКРОЙ" in out
    assert "дн" not in out.split("ЗАКРОЙ")[1].split("\n")[0]


def test_pending_exit_age_omitted_without_now():
    """Back-compat: older callers pass no `now` and must still render."""
    out = _render_orphan(_matches(), MARKS, pending_exit=_pend(13))
    assert "ЗАКРОЙ" in out
