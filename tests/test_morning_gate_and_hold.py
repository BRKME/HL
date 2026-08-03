"""Morning digest gate + manual hold override (03.08.2026).

GATE. The whitelist digest was folded into daily-monitor and gated on
`now.hour == 7` (07:00 UTC = 10:00 MSK). GitHub Actions never delivers the
07:00 cron inside hour 7 — measured ticks land at 08–10 UTC — so the branch
has not fired once since the standalone whitelist-focus cron was disabled on
06.07. Four weeks of verdict_raw and rs_30d/rs_90d collection were lost
silently. Gate is now «first run of the day at or after 07:00 UTC».

HOLD. When the operator deliberately holds a position against a standing
exit requirement, that decision should be recorded rather than nagged at,
so it lands in the stats instead of looking like a missed notification.
"""
import json
from datetime import datetime, timezone

import pytest

from src.morning_gate import should_run_digest, mark_digest_done
from src.manual_hold import (
    Hold, load_holds, set_hold, clear_hold, is_held,
)


def _t(day, hour):
    return datetime(2026, 8, day, hour, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------- gate

def test_digest_runs_on_first_tick_of_day(tmp_path):
    assert should_run_digest(_t(3, 9), tmp_path) is True


def test_digest_runs_even_when_cron_drifts_to_ten(tmp_path):
    """The exact failure: cron scheduled 07:00, delivered 10:xx."""
    assert should_run_digest(_t(3, 10), tmp_path) is True


def test_digest_does_not_run_twice_in_a_day(tmp_path):
    assert should_run_digest(_t(3, 9), tmp_path) is True
    mark_digest_done(_t(3, 9), tmp_path)
    assert should_run_digest(_t(3, 11), tmp_path) is False
    assert should_run_digest(_t(3, 19), tmp_path) is False


def test_digest_runs_again_next_day(tmp_path):
    mark_digest_done(_t(3, 9), tmp_path)
    assert should_run_digest(_t(4, 9), tmp_path) is True


def test_digest_skipped_before_seven_utc(tmp_path):
    """A manual pre-dawn dispatch shouldn't burn the day's digest."""
    assert should_run_digest(_t(3, 5), tmp_path) is False


def test_gate_survives_corrupt_state(tmp_path):
    (tmp_path / "morning_digest.json").write_text("{not json")
    assert should_run_digest(_t(3, 9), tmp_path) is True


# ------------------------------------------------------------------- hold

def test_no_holds_by_default(tmp_path):
    assert load_holds(tmp_path) == {}
    assert is_held("NEAR", tmp_path) is False


def test_set_and_read_hold(tmp_path):
    set_hold("NEAR", _t(3, 20), "жду отскок к 1.9", tmp_path)
    holds = load_holds(tmp_path)
    assert "NEAR" in holds
    assert isinstance(holds["NEAR"], Hold)
    assert holds["NEAR"].note == "жду отскок к 1.9"
    assert is_held("NEAR", tmp_path) is True
    assert is_held("ZEC", tmp_path) is False


def test_clear_hold(tmp_path):
    set_hold("NEAR", _t(3, 20), "", tmp_path)
    clear_hold("NEAR", tmp_path)
    assert is_held("NEAR", tmp_path) is False


def test_hold_is_persisted_as_json(tmp_path):
    set_hold("ZEC", _t(3, 20), "тест", tmp_path)
    raw = json.loads((tmp_path / "manual_holds.json").read_text())
    assert raw["ZEC"]["note"] == "тест"
    assert raw["ZEC"]["since"].startswith("2026-08-03")


def test_clear_unknown_coin_is_noop(tmp_path):
    clear_hold("BTC", tmp_path)
    assert load_holds(tmp_path) == {}


# ------------------------------------------------------- hold in the report

def test_held_position_shows_hold_line_not_close_line():
    from src.daily_report import _render_orphan
    from src.matcher import MatchResult
    from src.portfolio import AggregatedPerpPosition

    pos = AggregatedPerpPosition(
        coin="NEAR", net_size=100.0, weighted_entry=1.64, total_pnl=10.0,
        contributors=[("main", 100.0)], avg_leverage=5.0,
        max_liquidation_distance_pct=25.0,
    )
    m = [MatchResult(position=pos, decision=None, status="orphan")]
    pend = {"NEAR": {"ts": "2026-07-21T19:52:00+00:00",
                     "exit_reason": "verdict_flip",
                     "closed_direction": "LONG"}}
    holds = {"NEAR": Hold(coin="NEAR",
                          since=datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc),
                          note="жду отскок")}
    out = _render_orphan(m, {"NEAR": 1.7489}, pending_exit=pend,
                         now=_t(3, 20), holds=holds)
    assert "ЗАКРОЙ ПО ПОЛИТИКЕ" not in out
    assert "ДЕРЖУ ПРОТИВ СИСТЕМЫ" in out
    assert "жду отскок" in out
    assert "verdict_flip" in out   # the standing requirement is still named
