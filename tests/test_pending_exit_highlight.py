# -*- coding: utf-8 -*-
"""Подсветка неисполненного exit в портфельном отчёте (16.07).

Кейс: 15.07 20:40 гвард эмитировал EXIT (verdict_flip) по HYPE LONG,
оператор проигнорировал, а отчёт 21:07 показал позицию с 🟢 LONG (вердикт
успел мигнуть обратно) — сигнал утонул, на следующий день −$23 и SL на
волоске. Якорь подсветки — ЖУРНАЛ, не мигающий вердикт: пока последняя
эмитированная запись по монете — EXIT, а позиция в портфеле открыта,
отчёт кричит «ЗАКРОЙ». Эмиссия нового входа легитимизирует холд и гасит
подсветку (по замороженным правилам). Дисплей, вердикт-логика не тронута.
"""
from src.daily_report import pending_exits


def _exit_row(coin="HYPE", ts="2026-07-15T17:40:00+00:00", emitted=True):
    return {"ts": ts, "coin": coin, "direction": "EXIT",
            "exit_reason": "verdict_flip", "closed_direction": "LONG",
            "pnl_r": -0.11, "emitted": emitted}


def _entry_row(coin="HYPE", ts="2026-07-16T03:45:10+00:00", d="LONG"):
    return {"ts": ts, "coin": coin, "direction": d, "entry": 66.185,
            "emitted": True}


def test_exit_as_last_emitted_record_is_pending():
    p = pending_exits([_entry_row(ts="2026-07-14T09:00:00+00:00"), _exit_row()])
    assert "HYPE" in p
    assert p["HYPE"]["exit_reason"] == "verdict_flip"
    assert p["HYPE"]["closed_direction"] == "LONG"


def test_reentry_after_exit_clears_pending():
    """Утренний перевход 16.07 06:45 MSK легитимизировал холд."""
    p = pending_exits([_exit_row(), _entry_row()])
    assert p == {}


def test_suppressed_records_ignored():
    p = pending_exits([_exit_row(),
                       dict(_entry_row(), emitted=False,
                            suppressed_by="cooldown")])
    assert "HYPE" in p                      # подавленный вход не гасит


def test_multiple_coins_independent():
    rows = [_exit_row(),
            _entry_row(coin="MORPHO", d="LONG")]
    p = pending_exits(rows)
    assert set(p) == {"HYPE"}


def _orphan(net_size=3.4):
    from src.matcher import MatchResult
    from src.portfolio import AggregatedPerpPosition
    pos = AggregatedPerpPosition(
        coin="HYPE", net_size=net_size, weighted_entry=68.7,
        total_pnl=-23.0, contributors=[("main", net_size)],
        avg_leverage=1.0, max_liquidation_distance_pct=50.0)
    return MatchResult(position=pos, decision=None, status="orphan")


def test_render_orphan_shows_close_line():
    from src.daily_report import _render_orphan
    m = _orphan()
    block = _render_orphan(
        [m], marks={"HYPE": 67.3}, coin_verdicts={"HYPE": "WAIT"},
        pending_exit={"HYPE": {"exit_reason": "verdict_flip",
                               "ts": "2026-07-15T17:40:00+00:00",
                               "closed_direction": "LONG"}})
    assert "ЗАКРОЙ" in block
    assert "verdict_flip" in block
    assert "15.07 20:40" in block           # МСК в тексте


def test_render_orphan_quiet_without_pending():
    from src.daily_report import _render_orphan
    m = _orphan()
    block = _render_orphan([m], marks={"HYPE": 67.3},
                           coin_verdicts={"HYPE": "WAIT"}, pending_exit={})
    assert "ЗАКРОЙ" not in block


def test_pending_exit_direction_mismatch_not_flagged():
    """EXIT закрывал LONG, а в портфеле SHORT — чужой сигнал, не кричим."""
    from src.daily_report import _render_orphan
    m = _orphan(net_size=-3.4)
    block = _render_orphan(
        [m], marks={"HYPE": 67.3},
        pending_exit={"HYPE": {"exit_reason": "verdict_flip",
                               "ts": "2026-07-15T17:40:00+00:00",
                               "closed_direction": "LONG"}})
    assert "ЗАКРОЙ" not in block


# ── двойственность вердиктов: показываем оба источника при расхождении ──────

def test_verdict_duality_shown_when_sources_differ():
    """16.07: отчёт показывал ⚪ WAIT (дневной whitelist), а гвард живёт по
    тактическому вердикту — противоречие в одном сообщении сбивало с толку.
    При расхождении показываем оба с подписями; при совпадении — как раньше."""
    from src.daily_report import _render_orphan
    m = _orphan()
    block = _render_orphan(
        [m], marks={"HYPE": 67.3},
        coin_verdicts={"HYPE": "WAIT"},
        tactical_verdicts={"HYPE": "LONG"})
    assert "⚪ WAIT дн." in block
    assert "🟢 LONG такт." in block


def test_verdict_single_when_sources_agree():
    from src.daily_report import _render_orphan
    m = _orphan()
    block = _render_orphan(
        [m], marks={"HYPE": 67.3},
        coin_verdicts={"HYPE": "LONG"},
        tactical_verdicts={"HYPE": "LONG"})
    assert "дн." not in block and "такт." not in block
    assert "🟢 LONG" in block
