# -*- coding: utf-8 -*-
"""Баг 15.07: гвард закрыл позицию (last_action_verdict=None), но следующий
тактический прогон под кулдауном молча воскресил action_verdict без записи
в журнал и без алерта. Следствия: (а) state противоречит журналу, гвард
каждый прогон молча обнуляет state — пинг-понг; (б) после истечения кулдауна
переэмиссия не происходит (prev == verdict), позиция остаётся без защиты
гварда навсегда, пока вердикт сам не сменится.

Инвариант: last_action_verdict становится не-None ТОЛЬКО через эмиссию
(журнал + алерт). Явный None не воскрешается и не маскируется fallback'ом
на last_verdict.
"""
from datetime import datetime, timedelta, timezone

from src.tactical_signals import (COOLDOWN_HOURS, next_state_entry,
                                  prev_action_verdict, should_emit)

NOW = datetime(2026, 7, 15, 17, 43, tzinfo=timezone.utc)
RECENT = (NOW - timedelta(hours=3)).isoformat()      # кулдаун активен
STALE = (NOW - timedelta(hours=COOLDOWN_HOURS + 1)).isoformat()


def _closed_by_guard():
    """State HYPE сразу после verdict_flip-выхода гварда (кейс 17:40 UTC)."""
    return {"last_verdict": "WAIT", "last_action_verdict": None,
            "last_alert_ts": RECENT, "last_change_ts": RECENT}


# --- prev: явный None — это «закрыто», а не «спроси last_verdict» ----------

def test_prev_explicit_none_is_closed_not_fallback():
    assert prev_action_verdict(_closed_by_guard()) is None


def test_prev_legacy_state_without_action_field_falls_back():
    assert prev_action_verdict({"last_verdict": "LONG"}) == "LONG"


def test_prev_open_position_survives_wait():
    st = {"last_verdict": "WAIT", "last_action_verdict": "SHORT"}
    assert prev_action_verdict(st) == "SHORT"


# --- сценарий 17:40 -> 17:43: воскрешения быть не должно --------------------

def test_no_resurrection_under_cooldown():
    st = _closed_by_guard()
    prev = prev_action_verdict(st)
    assert should_emit("LONG", prev, st["last_alert_ts"], NOW) is False
    new = next_state_entry(st, "LONG", NOW.isoformat())
    assert new["last_action_verdict"] is None      # закрыто — значит закрыто
    assert new["last_verdict"] == "LONG"
    assert new["last_change_ts"] == RECENT          # смены действия не было


def test_reentry_emits_after_cooldown_expires():
    st = dict(_closed_by_guard(), last_alert_ts=STALE)
    prev = prev_action_verdict(st)
    assert should_emit("LONG", prev, st["last_alert_ts"], NOW) is True


# --- регресс 21.06: SHORT -> WAIT -> SHORT без повторного алерта ------------

def test_wait_dip_keeps_action_and_no_realert():
    st = {"last_verdict": "SHORT", "last_action_verdict": "SHORT",
          "last_alert_ts": STALE, "last_change_ts": STALE}
    mid = next_state_entry(st, "WAIT", NOW.isoformat())
    assert mid["last_action_verdict"] == "SHORT"
    assert mid["last_change_ts"] == STALE
    prev = prev_action_verdict(mid)
    assert should_emit("SHORT", prev, mid["last_alert_ts"], NOW) is False


# --- существующее поведение: смена стороны открытого действия под кулдауном -

def test_open_position_side_change_under_cooldown_still_tracked():
    st = {"last_verdict": "LONG", "last_action_verdict": "LONG",
          "last_alert_ts": RECENT, "last_change_ts": RECENT}
    new = next_state_entry(st, "SHORT", NOW.isoformat())
    assert new["last_action_verdict"] == "SHORT"
    assert new["last_change_ts"] == NOW.isoformat()
