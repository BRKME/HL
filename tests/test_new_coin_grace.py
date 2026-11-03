"""Только что добавленная монета — не «пропавшая» (03.11.2026).

LIT добавлен 02.11 в 20:03 UTC. Детектор журнала стоит на 16:00 UTC, но
GitHub запустил его в 23:19 — уже на коде с LIT, а первый дневной сбор с
LIT будет только 03.11. В 02:19 МСК в канал ушло «монета LIT отсутствует
в журнале» — ложная тревога, и именно ночью. Задержки расписания Actions
здесь обычны (31.10 — 19:44, 01.11 — 20:15 при плане 16:00).
"""
from datetime import datetime, timedelta, timezone

from src.journal_health import NEW_COIN_GRACE_H, check_coin_coverage

ADDED = {"LIT": "2026-11-02T20:03:00+00:00"}
T_ADD = datetime(2026, 11, 2, 20, 3, tzinfo=timezone.utc)


def _e(coin, ts):
    return {"coin": coin, "ts": ts.isoformat()}


def test_new_coin_without_entries_is_quiet_within_grace():
    now = T_ADD + timedelta(hours=3, minutes=16)       # ровно случай 02.11
    entries = [_e("BTC", now - timedelta(hours=5))]
    assert check_coin_coverage(entries, ["BTC", "LIT"], now, added=ADDED) == []


def test_new_coin_still_missing_after_grace_is_reported():
    now = T_ADD + timedelta(hours=NEW_COIN_GRACE_H + 1)
    entries = [_e("BTC", now - timedelta(hours=5))]
    issues = check_coin_coverage(entries, ["BTC", "LIT"], now, added=ADDED)
    assert [i.message for i in issues] == ["монета LIT отсутствует в журнале"]


def test_old_coin_missing_is_reported_as_before():
    now = T_ADD
    issues = check_coin_coverage([], ["ONDO"], now, added=ADDED)
    assert issues and "ONDO" in issues[0].message


def test_grace_covers_first_daily_run_plus_actions_delay():
    assert NEW_COIN_GRACE_H >= 36


def test_added_registry_matches_universe():
    from src.whitelist_focus import COINS_ADDED, FOCUS_COINS
    assert set(COINS_ADDED) <= set(FOCUS_COINS)
    for ts in COINS_ADDED.values():
        datetime.fromisoformat(ts)           # формат читается
