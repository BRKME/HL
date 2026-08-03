"""Journal health checks (03.08.2026).

Post-mortem: whitelist_focus stopped writing on 06.07 and nobody noticed for
four weeks. The existing healthcheck had two defects that let it through —

  * `raw_recorded == 0` / `rs_30_count == 0` are absolute-zero tests. 594
    historical records existed, so neither ever fired again after the stream
    died. What matters is not «was this field ever written» but «was it
    written recently».
  * staleness was measured on the journal as a whole. daily_monitor kept
    writing every two hours, so the journal looked alive while two of its
    three sources were dead.

Both checks are now per-stream. A stream that has produced nothing in
STALE_HOURS is a warning regardless of how much history it has.
"""
from datetime import datetime, timedelta, timezone

from src.journal_health import (
    STALE_HOURS,
    check_coin_coverage,
    check_field_staleness,
    check_source_staleness,
    run_checks,
    worst_severity,
)

NOW = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)


def _e(hours_ago, source="daily_monitor", coin="BTC", **extra):
    rec = {"ts": (NOW - timedelta(hours=hours_ago)).isoformat(),
           "source": source, "coin": coin, "verdict": "WAIT"}
    rec.update(extra)
    return rec


# ------------------------------------------------------- source staleness

def test_fresh_sources_produce_no_issues():
    entries = [_e(1, "daily_monitor"), _e(2, "whitelist_focus")]
    assert check_source_staleness(entries, NOW) == []


def test_dead_source_is_flagged_even_with_long_history():
    """The exact 06.07 failure: 594 old records, none recent."""
    entries = [_e(24 * 30 + h, "whitelist_focus") for h in range(50)]
    entries += [_e(1, "daily_monitor")]
    issues = check_source_staleness(entries, NOW)
    assert len(issues) == 1
    assert "whitelist_focus" in issues[0].message


def test_source_just_inside_threshold_is_quiet():
    entries = [_e(STALE_HOURS - 1, "whitelist_focus")]
    assert check_source_staleness(entries, NOW) == []


def test_source_just_outside_threshold_warns():
    entries = [_e(STALE_HOURS + 1, "whitelist_focus")]
    assert len(check_source_staleness(entries, NOW)) == 1


# -------------------------------------------------------- field staleness

def test_field_written_recently_is_fine():
    entries = [_e(1, rs_30d=5.0), _e(3, rs_30d=4.0)]
    assert check_field_staleness(entries, NOW, "rs_30d") == []


def test_field_that_stopped_being_written_is_flagged():
    entries = [_e(24 * 30, rs_30d=5.0), _e(1), _e(3)]
    issues = check_field_staleness(entries, NOW, "rs_30d")
    assert len(issues) == 1
    assert "rs_30d" in issues[0].message


def test_field_never_written_is_critical():
    entries = [_e(1), _e(3)]
    issues = check_field_staleness(entries, NOW, "verdict_raw")
    assert len(issues) == 1
    assert issues[0].severity == "critical"


# --------------------------------------------------------- coin coverage

def test_all_expected_coins_present():
    entries = [_e(1, coin=c) for c in ("BTC", "ETH")]
    assert check_coin_coverage(entries, ("BTC", "ETH"), NOW) == []


def test_missing_coin_is_flagged():
    """ASTER is in FOCUS_COINS but absent from the journal entirely."""
    entries = [_e(1, coin=c) for c in ("BTC", "ETH")]
    issues = check_coin_coverage(entries, ("BTC", "ETH", "ASTER"), NOW)
    assert len(issues) == 1
    assert "ASTER" in issues[0].message


def test_coin_that_went_stale_is_flagged():
    entries = [_e(1, coin="BTC"), _e(24 * 10, coin="ASTER")]
    issues = check_coin_coverage(entries, ("BTC", "ASTER"), NOW)
    assert len(issues) == 1
    assert "ASTER" in issues[0].message


# ------------------------------------------------------------- aggregate

def test_run_checks_on_healthy_journal():
    entries = [_e(h, src, coin=c, rs_30d=1.0, verdict_raw="WAIT")
               for h in (1, 3) for src in ("daily_monitor", "whitelist_focus")
               for c in ("BTC", "ETH")]
    assert run_checks(entries, NOW, ("BTC", "ETH")) == []


def test_run_checks_catches_the_july_regression():
    """Reproduces 03.08 reality: only daily_monitor alive, no raw, no RS."""
    entries = [_e(24 * 30 + h, "whitelist_focus", coin="BTC",
                  rs_30d=1.0, verdict_raw="WAIT") for h in range(20)]
    entries += [_e(h, "daily_monitor", coin="BTC") for h in (1, 3, 5)]
    issues = run_checks(entries, NOW, ("BTC",))
    msgs = " ".join(i.message for i in issues)
    assert "whitelist_focus" in msgs
    assert "rs_30d" in msgs
    assert "verdict_raw" in msgs


def test_empty_journal_is_critical():
    issues = run_checks([], NOW, ("BTC",))
    assert issues
    assert worst_severity(issues) == "critical"


def test_worst_severity_ranking():
    assert worst_severity([]) is None
    from src.journal_health import HealthIssue
    assert worst_severity([HealthIssue("warn", "a")]) == "warn"
    assert worst_severity([HealthIssue("warn", "a"),
                           HealthIssue("critical", "b")]) == "critical"


def test_retired_source_does_not_warn():
    """eth_focus was retired on purpose — its silence isn't a fault."""
    entries = [_e(24 * 60, "eth_focus"), _e(1, "daily_monitor")]
    assert check_source_staleness(entries, NOW) == []
