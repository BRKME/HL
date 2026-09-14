"""Ночное окно тишины для китовых алертов (14.09.2026).

Письмо в 01:04 про одного кита. Мгновенный алерт ночью оператор всё равно
не исполнит — он либо срочный, либо не нужен вовсе, а разбудить может.
Ночью всё уходит в дайджест, который отправится утром.

Границы по московскому времени: оператор живёт по нему, а прогоны идут в
UTC, и путать эти шкалы — верный способ сделать окно на три часа не там.
"""
from datetime import datetime, timezone

import pytest

from src.whale_report import QUIET_END_MSK, QUIET_START_MSK, in_quiet_hours


def _utc(h):
    return datetime(2026, 9, 14, h, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("utc_hour,msk,quiet", [
    (22, 1, True),      # 01:04 МСК — ровно случай оператора
    (1, 4, True),
    (3, 6, True),
    (4, 7, False),      # 07:00 МСК — тишина кончилась
    (9, 12, False),
    (19, 22, False),
    (20, 23, True),     # 23:00 МСК — тишина началась
])
def test_quiet_window_by_moscow_time(utc_hour, msk, quiet):
    assert in_quiet_hours(_utc(utc_hour)) is quiet


def test_window_crosses_midnight():
    assert QUIET_START_MSK > QUIET_END_MSK


def test_night_signals_go_to_digest_not_instant():
    """Ровно поведение: ночью мгновенных нет, всё копится."""
    from src.whale_report import split_by_mode
    from src.signal_backtester import Signal as _S

    class S:
        severity = 3
        rule = "WHALE_CLUSTER"
        coin = "ZEC"
        message = "тест"
        details = {}

    instant, digest = split_by_mode([S()], now=_utc(22))
    assert instant == []
    assert len(digest) == 1


def test_day_signals_stay_instant():
    from src.whale_report import split_by_mode

    class S:
        severity = 3
        rule = "WHALE_CLUSTER"
        coin = "ZEC"
        message = "тест"
        details = {}

    instant, digest = split_by_mode([S()], now=_utc(9))
    assert len(instant) == 1
    assert digest == []


def test_without_now_behaviour_unchanged():
    """Старые вызовы без времени не должны менять поведение."""
    from src.whale_report import split_by_mode

    class S:
        severity = 3
        rule = "WHALE_CLUSTER"
        coin = "ZEC"
        message = "тест"
        details = {}

    instant, _ = split_by_mode([S()])
    assert len(instant) == 1
