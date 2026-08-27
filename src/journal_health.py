"""Staleness detection for the verdict journal.

Written after a four-week silent outage (see tests/test_journal_health.py
for the post-mortem). The governing idea: every stream the analysis depends
on — each source, each observability field, each coin in the universe —
must be able to raise its own alarm. A journal is not healthy because it is
growing; it is healthy when every stream inside it is still growing.

Checks are pure functions over already-loaded entries so they can be tested
without touching disk, and so the same logic serves both the CLI report and
the scheduled workflow.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence

# A stream that has produced nothing in this many hours is stale. Sized
# against the slowest legitimate writer: the morning digest runs once a
# day, so 36h tolerates one missed run without crying wolf, and catches
# two in a row.
STALE_HOURS = 36

# Порог свой на источник, потому что частота записи у них разная (27.08).
# Единый порог в 36 часов был сделан под источник, пишущий каждые два часа:
# он терпит один пропущенный прогон. Но дайджест СУТОЧНЫЙ, и пропуск ровно
# одного дня даёт 24–30 часов — всегда меньше 36. 27.08 daily-monitor не
# писал 32 часа, детектор промолчал, и день прошёл без единой сводки.
# 30 часов = сутки + 6 на задержку доставки Actions.
SOURCE_STALE_HOURS = {
    "whitelist_focus": 30,
}


def stale_threshold_for(source: str) -> int:
    return SOURCE_STALE_HOURS.get(source, STALE_HOURS)

# Fields whose collection the end-of-August analysis depends on. These are
# exactly the two that died unnoticed on 06.07.
TRACKED_FIELDS = ("verdict_raw", "rs_30d")

# Sources deliberately retired — their silence is a decision, not a fault.
# eth_focus was superseded by the whitelist verdicts; its cron is commented
# out in .github/workflows/eth-focus.yml and kept only for manual runs.
RETIRED_SOURCES = ("eth_focus",)

# Источники, чьё молчание может быть законным. daily_monitor пишет вердикты
# только по ОТКРЫТЫМ позициям: вне рынка ему нечего журналить, и тишина —
# правда о портфеле, а не отказ. 21.08 детектор поднял по нему ложную
# тревогу; ложная тревога обесценивает детектор быстрее пропущенной.
# Если замолчали ВСЕ источники — оправдание снимается, тишина уже не
# объясняется отсутствием позиций.
CONDITIONAL_SOURCES = ("daily_monitor",)

_SEVERITY_RANK = {"warn": 1, "critical": 2}


@dataclass(frozen=True)
class HealthIssue:
    severity: str   # "warn" | "critical"
    message: str


def worst_severity(issues: Sequence[HealthIssue]) -> Optional[str]:
    if not issues:
        return None
    return max((i.severity for i in issues), key=lambda s: _SEVERITY_RANK.get(s, 0))


def _parse_ts(value) -> Optional[datetime]:
    try:
        ts = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def _age_hours(ts: datetime, now: datetime) -> float:
    return (now - ts).total_seconds() / 3600


def _latest(entries: Iterable[dict], predicate) -> Optional[datetime]:
    stamps = [_parse_ts(e.get("ts")) for e in entries if predicate(e)]
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def _fmt_age(hours: float) -> str:
    return f"{hours / 24:.0f} дн" if hours >= 48 else f"{hours:.0f} ч"


# ------------------------------------------------------------------ checks

def check_source_staleness(entries: Sequence[dict], now: datetime,
                           stale_hours: int = STALE_HOURS) -> list[HealthIssue]:
    """Each source that has ever written must still be writing."""
    issues: list[HealthIssue] = []
    sources = {e.get("source") for e in entries if e.get("source")}
    sources -= set(RETIRED_SOURCES)

    ages = {}
    for src in sorted(s for s in sources if s):
        last = _latest(entries, lambda e, s=src: e.get("source") == s)
        if last is not None:
            ages[src] = (last, _age_hours(last, now))

    unconditional_alive = any(
        age <= min(stale_hours, stale_threshold_for(src))
        for src, (_, age) in ages.items()
        if src not in CONDITIONAL_SOURCES)

    for src, (last, age) in ages.items():
        if (src in CONDITIONAL_SOURCES and unconditional_alive):
            continue
        if age > min(stale_hours, stale_threshold_for(src)):
            issues.append(HealthIssue(
                "warn",
                f"источник {src} молчит {_fmt_age(age)} "
                f"(последняя запись {last.date()})"))
    return issues


def check_field_staleness(entries: Sequence[dict], now: datetime, field: str,
                          stale_hours: int = STALE_HOURS) -> list[HealthIssue]:
    """A field that stopped being populated is invisible in a coverage ratio."""
    last = _latest(entries, lambda e: e.get(field) is not None)
    if last is None:
        return [HealthIssue("critical",
                            f"поле {field} не пишется вообще")]
    age = _age_hours(last, now)
    if age > stale_hours:
        return [HealthIssue(
            "warn",
            f"поле {field} не пишется {_fmt_age(age)} "
            f"(последняя запись {last.date()})")]
    return []


def check_coin_coverage(entries: Sequence[dict], expected_coins: Sequence[str],
                        now: datetime,
                        stale_hours: int = STALE_HOURS) -> list[HealthIssue]:
    """Every coin in the universe must be represented recently."""
    issues: list[HealthIssue] = []
    for coin in expected_coins:
        last = _latest(entries, lambda e, c=coin: e.get("coin") == c)
        if last is None:
            issues.append(HealthIssue(
                "warn", f"монета {coin} отсутствует в журнале"))
            continue
        age = _age_hours(last, now)
        if age > stale_hours:
            issues.append(HealthIssue(
                "warn",
                f"монета {coin} не журналится {_fmt_age(age)} "
                f"(последняя запись {last.date()})"))
    return issues


def run_checks(entries: Sequence[dict], now: datetime,
               expected_coins: Sequence[str],
               stale_hours: int = STALE_HOURS) -> list[HealthIssue]:
    if not entries:
        return [HealthIssue("critical", "журнал пуст — бот не пишет")]

    issues: list[HealthIssue] = []
    issues += check_source_staleness(entries, now, stale_hours)
    for field in TRACKED_FIELDS:
        issues += check_field_staleness(entries, now, field, stale_hours)
    issues += check_coin_coverage(entries, expected_coins, now, stale_hours)
    return issues
