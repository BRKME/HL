"""Наблюдаемость тактики: когда вердикт НЕ меняется, состояние всё равно
фиксирует текущий вердикт + дату последней смены — чтобы heartbeat мог
показать 'BTC SHORT, без смены N дней' и молчание читалось как осознанное,
а не как зависание (смущение оператора 16.06).
"""
from datetime import datetime, timezone

from src.tactical_signals import tactical_state_summary


def test_summary_shows_current_verdicts():
    state = {
        "BTC": {"last_verdict": "SHORT", "last_change_ts": "2026-06-11T16:33:00+00:00"},
        "ETH": {"last_verdict": "SHORT", "last_change_ts": "2026-06-11T20:40:00+00:00"},
    }
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    s = tactical_state_summary(state, now=now)
    assert "BTC" in s and "SHORT" in s
    assert "4" in s            # дней без смены (11 июн 16:33 -> 16 июн 12:00 = 4 полных)

def test_summary_empty_state():
    s = tactical_state_summary({}, now=datetime(2026, 6, 16, tzinfo=timezone.utc))
    assert s  # не падает, что-то осмысленное

def test_summary_handles_missing_change_ts():
    state = {"BTC": {"last_verdict": "LONG"}}
    s = tactical_state_summary(state, now=datetime(2026, 6, 16, tzinfo=timezone.utc))
    assert "BTC" in s and "LONG" in s
