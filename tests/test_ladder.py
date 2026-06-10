"""Тесты src/ladder.py — чтение контракта cycle_ladder.json из OracAI.

Лестница — стратегический слой (core-капитал на цикл), planner — тактический
(недельный DCA на перпах). Вердикты planner'а лестница НЕ меняет; она даёт
контекстный блок в субботний отчёт, чтобы «SKIP недели» и «BUY 30% капитала»
не выглядели противоречием.

Fail-safe по спеке контракта: файл недоступен или date_utc старше 3 дней →
блок не показывается (None), никакого падения — лестница информационна,
в отличие от snapshot, который обязан падать громко.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from src import ladder


def _contract(**over):
    base = {
        "policy_version": "ladder-v1.2",
        "zone": "NEUTRAL",
        "drawdown_call": "AMBIGUOUS",
        "mvrv": 1.15,
        "dca_multiplier": 1.5,
        "signal": {
            "policy_version": "ladder-v1.2",
            "action": "HOLD",
            "fraction_of_capital": 0.0,
            "fraction_of_stack": 0.0,
            "trigger": "no_event",
            "rationale": "Зона NEUTRAL без события",
        },
        "date_utc": date.today().isoformat(),
        "price": 61400.0,
    }
    base.update(over)
    return base


class TestFetchLadder:
    def test_fresh_contract_parsed(self, monkeypatch):
        monkeypatch.setattr(ladder, "_http_get_json", lambda url: _contract())
        out = ladder.fetch_ladder()
        assert out is not None
        assert out["zone"] == "NEUTRAL"
        assert out["signal"]["action"] == "HOLD"

    def test_stale_contract_dropped(self, monkeypatch):
        old = (date.today() - timedelta(days=4)).isoformat()
        monkeypatch.setattr(ladder, "_http_get_json",
                            lambda url: _contract(date_utc=old))
        assert ladder.fetch_ladder() is None

    def test_three_days_is_still_fresh(self, monkeypatch):
        ok = (date.today() - timedelta(days=3)).isoformat()
        monkeypatch.setattr(ladder, "_http_get_json",
                            lambda url: _contract(date_utc=ok))
        assert ladder.fetch_ladder() is not None

    def test_network_error_soft_fails(self, monkeypatch):
        def boom(url):
            raise RuntimeError("offline")
        monkeypatch.setattr(ladder, "_http_get_json", boom)
        assert ladder.fetch_ladder() is None     # no exception

    def test_missing_signal_field_dropped(self, monkeypatch):
        c = _contract()
        del c["signal"]
        monkeypatch.setattr(ladder, "_http_get_json", lambda url: c)
        assert ladder.fetch_ladder() is None


class TestRenderContext:
    def test_context_for_hold(self):
        ctx = ladder.render_context(_contract())
        assert ctx["zone"] == "NEUTRAL"
        assert ctx["event_line"] is None          # HOLD -> нет события недели
        assert "1.15" in ctx["status_line"] or "NEUTRAL" in ctx["status_line"]

    def test_context_for_buy_event(self):
        c = _contract(signal={
            "policy_version": "ladder-v1.2", "action": "BUY",
            "fraction_of_capital": 0.30, "fraction_of_stack": 0.0,
            "trigger": "entered_NEUTRAL", "rationale": "Вход в зону NEUTRAL",
        })
        ctx = ladder.render_context(c)
        assert ctx["event_line"] is not None
        assert "30%" in ctx["event_line"]
        assert "ПОКУПКА" in ctx["event_line"].upper() or "BUY" in ctx["event_line"].upper()

    def test_context_for_sell_event(self):
        c = _contract(signal={
            "policy_version": "ladder-v1.2", "action": "SELL",
            "fraction_of_capital": 0.0, "fraction_of_stack": 0.25,
            "trigger": "trend_break_after_distribution",
            "rationale": "Тренд сломан",
        })
        ctx = ladder.render_context(c)
        assert "25%" in ctx["event_line"]

    def test_none_contract_gives_none(self):
        assert ladder.render_context(None) is None


class TestRenderIntegration:
    def test_skip_report_carries_ladder_block(self):
        from src import render
        signal = {"signal": "SKIP", "leverage": 0,
                  "reasons": ["Рынок не в бычьей фазе — пропускаем неделю"],
                  "raw": {}}
        ctx = ladder.render_context(_contract(signal={
            "policy_version": "ladder-v1.2", "action": "BUY",
            "fraction_of_capital": 0.30, "fraction_of_stack": 0.0,
            "trigger": "entered_NEUTRAL", "rationale": "Вход в зону NEUTRAL",
        }))
        msg = render.render_report(signal=signal, picks=[], skipped=[],
                                   ladder_ctx=ctx)
        # контраст в одном сообщении: тактический SKIP + стратегическая покупка
        assert "не покупаем" in msg.lower()
        assert "30% капитала" in msg
        assert "NEUTRAL" in msg

    def test_skip_report_without_ladder_unchanged(self):
        from src import render
        signal = {"signal": "SKIP", "leverage": 0, "reasons": [], "raw": {}}
        msg = render.render_report(signal=signal, picks=[], skipped=[],
                                   ladder_ctx=None)
        assert "Зона цикла" not in msg     # fail-safe: блока просто нет
