"""Пороги свежести по частоте источника (27.08.2026).

27.08 не пришёл ни дайджест, ни отчёт: daily-monitor не писал с 26.08 07:41.
Детектор промолчал — на момент его прогона в 16:00 UTC простой был 32 часа
при пороге 36.

Это не невезение, а структурная дыра. Дайджест суточный: пропуск ровно
одного дня даёт 24–30 часов, что ВСЕГДА меньше 36. Единый порог, сделанный
под источник, пишущий каждые два часа, не способен поймать пропуск дня у
источника, пишущего раз в сутки.

Порог теперь свой на источник: сутки плюс запас на задержку Actions.
"""
from datetime import datetime, timedelta, timezone

from src.journal_health import (
    SOURCE_STALE_HOURS,
    STALE_HOURS,
    check_source_staleness,
    stale_threshold_for,
)

NOW = datetime(2026, 8, 27, 16, 0, tzinfo=timezone.utc)


def _e(hours_ago, source, coin="BTC"):
    return {"ts": (NOW - timedelta(hours=hours_ago)).isoformat(),
            "source": source, "coin": coin, "verdict": "WAIT"}


def test_daily_source_has_tighter_threshold():
    assert stale_threshold_for("whitelist_focus") < STALE_HOURS


def test_unknown_source_keeps_default():
    assert stale_threshold_for("что-то новое") == STALE_HOURS


def test_the_missed_day_is_now_caught():
    """Ровно 27.08: простой 32 часа у суточного источника."""
    entries = [_e(32, "whitelist_focus"), _e(1, "daily_monitor")]
    issues = check_source_staleness(entries, NOW)
    assert len(issues) == 1
    assert "whitelist_focus" in issues[0].message


def test_normal_daily_cadence_is_quiet():
    """Сутки плюс задержка Actions — это норма, а не тревога."""
    entries = [_e(26, "whitelist_focus"), _e(1, "daily_monitor")]
    assert check_source_staleness(entries, NOW) == []


def test_threshold_leaves_room_for_actions_delay():
    """Между нормой (26 ч) и пропуском дня (32 ч) должен быть зазор."""
    t = stale_threshold_for("whitelist_focus")
    assert 26 < t < 32


def test_conditional_source_still_excused():
    """daily_monitor молчит вне рынка — это по-прежнему законно."""
    entries = [_e(1, "whitelist_focus"), _e(50, "daily_monitor")]
    assert check_source_staleness(entries, NOW) == []


def test_every_configured_source_is_known():
    for src in SOURCE_STALE_HOURS:
        assert stale_threshold_for(src) == SOURCE_STALE_HOURS[src]
