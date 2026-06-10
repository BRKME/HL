"""Тесты src/tactical_signals.py — событийные сигналы «система поймала движение».

Слой над вердиктным движком: алерт уходит в момент СМЕНЫ вердикта на
LONG/SHORT (не дайджестом), с фандингом и позицией китов в сообщении.
Киты — фильтр эмиссии и контекст, НЕ компонент вердикта (вес китов в
вердикте сознательно обнулён до валидации; A/B-журнал остаётся чистым).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src import tactical_signals as tsig

NOW = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)


def _ws(coin="BTC", rule="WHALE_NEW_OPEN", direction="long",
        notional=50_000, hours_ago=4):
    ts = (NOW - timedelta(hours=hours_ago)).isoformat()
    details = {"coin": coin, "notional_usd": notional}
    if rule == "WHALE_FLIP":
        details["to_side"] = direction
    elif rule == "WHALE_NEW_OPEN":
        details["direction"] = direction
    elif rule == "WHALE_OVERLAP":
        details["whale_side"] = direction
    return {"run_ts": ts, "rule": rule, "coin": coin, "details": details}


class TestWhaleStance:
    def test_net_long_from_opens(self):
        sigs = [_ws(direction="long"), _ws(direction="long"),
                _ws(direction="short")]
        assert tsig.whale_stance(sigs, "BTC", NOW) == "long"

    def test_flip_uses_to_side(self):
        sigs = [_ws(rule="WHALE_FLIP", direction="short"),
                _ws(rule="WHALE_FLIP", direction="short")]
        assert tsig.whale_stance(sigs, "BTC", NOW) == "short"

    def test_small_notional_ignored(self):
        # шум вроде флипов на $176 не должен формировать stance
        sigs = [_ws(direction="short", notional=176),
                _ws(direction="short", notional=200)]
        assert tsig.whale_stance(sigs, "BTC", NOW) is None

    def test_stale_signals_ignored(self):
        sigs = [_ws(direction="long", hours_ago=100)]
        assert tsig.whale_stance(sigs, "BTC", NOW) is None

    def test_mixed_is_none(self):
        sigs = [_ws(direction="long"), _ws(direction="short")]
        assert tsig.whale_stance(sigs, "BTC", NOW) is None

    def test_other_coin_ignored(self):
        sigs = [_ws(coin="ZEC", direction="short")]
        assert tsig.whale_stance(sigs, "BTC", NOW) is None


class TestShouldEmit:
    def test_emit_on_change_to_long(self):
        assert tsig.should_emit("LONG", prev_verdict="WAIT",
                                last_alert_ts=None, now=NOW) is True

    def test_no_emit_when_unchanged(self):
        assert tsig.should_emit("LONG", prev_verdict="LONG",
                                last_alert_ts=None, now=NOW) is False

    def test_wait_never_emits(self):
        assert tsig.should_emit("WAIT", prev_verdict="SHORT",
                                last_alert_ts=None, now=NOW) is False

    def test_cooldown_blocks_repeat(self):
        recent = (NOW - timedelta(hours=3)).isoformat()
        assert tsig.should_emit("LONG", prev_verdict="WAIT",
                                last_alert_ts=recent, now=NOW) is False

    def test_cooldown_expires(self):
        old = (NOW - timedelta(hours=tsig.COOLDOWN_HOURS + 1)).isoformat()
        assert tsig.should_emit("SHORT", prev_verdict="WAIT",
                                last_alert_ts=old, now=NOW) is True


class TestWhaleFilter:
    def test_long_blocked_by_net_short_whales(self):
        ok, note = tsig.whale_filter("LONG", "short")
        assert ok is False
        assert "кит" in note.lower()

    def test_short_blocked_by_net_long_whales(self):
        ok, _ = tsig.whale_filter("SHORT", "long")
        assert ok is False

    def test_aligned_passes(self):
        assert tsig.whale_filter("LONG", "long")[0] is True
        assert tsig.whale_filter("SHORT", "short")[0] is True

    def test_no_stance_passes(self):
        ok, note = tsig.whale_filter("LONG", None)
        assert ok is True


class TestStopLoss:
    def test_long_sl_below_entry(self):
        sl = tsig.sl_for("LONG", entry=100.0, atr=4.0,
                         swing_low=95.0, swing_high=120.0)
        # max(entry - 2*ATR, swing_low) = max(92, 95) = 95
        assert sl == 95.0

    def test_short_sl_above_entry(self):
        sl = tsig.sl_for("SHORT", entry=100.0, atr=4.0,
                         swing_low=80.0, swing_high=104.0)
        # min(entry + 2*ATR, swing_high) = min(108, 104) = 104
        assert sl == 104.0


class TestAlertText:
    def test_alert_carries_everything(self):
        msg = tsig.build_alert(
            coin="BTC", direction="LONG", entry=61400.0, sl=58900.0,
            rationale="отскок в нисходящем тренде, oversold (RSI 28)",
            funding_apr_pct=-12.5, whale_note="киты нетто-LONG (3 сигнала, $240k)",
            regime="TRANSITION",
        )
        for part in ("BTC", "LONG", "61", "58", "фандинг", "-12", "кит",
                     "TRANSITION"):
            assert part.lower() in msg.lower() or part in msg
