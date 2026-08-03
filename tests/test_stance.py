"""Показывать сырой сигнал и подсвечивать позицию против системы (03.08).

Разбор: я сказал, что по NEAR, HYPE и MORPHO система «структурно не умеет
сказать вниз». Это было неверно, и цифры это опровергают:

    монета   raw LONG  raw SHORT  raw WAIT | fin LONG  fin SHORT  fin WAIT
    HYPE           99          0         0 |       38          0        61
    NEAR           53          0        46 |       28          0        71
    BTC             0         58        41 |       31         38        30

По HYPE и NEAR сырой слой ни разу не увидел нисходящего тренда — блокировки
там нет вообще. А вот по BTC и ETH сырой слой не выдал НИ ОДНОГО лонга, при
этом в финале их 31 и 27; и 60 сырых шортов погашены в WAIT — все до одного
при regime=BULL, инвариантом иерархии.

То есть система говорить «вниз» умеет, просто в этот момент её затыкает
стратегический слой, и оператор видит нейтральный ⚪ WAIT вместо «сетап на
шорт есть, но запрещён режимом». Решение — не менять логику вердикта
(инвариант остаётся, выборка остаётся сравнимой), а перестать прятать
сырой сигнал и явно помечать позицию, стоящую против него.
"""
import pytest

from src.stance import Stance, position_stance, format_verdict_pair


# ------------------------------------------------------------ position_stance

def test_long_with_long_verdict_is_aligned():
    assert position_stance("LONG", "LONG", "LONG") is Stance.ALIGNED


def test_long_with_short_verdict_is_against():
    assert position_stance("LONG", "SHORT", "SHORT") is Stance.AGAINST


def test_short_with_long_verdict_is_against():
    assert position_stance("SHORT", "LONG", "LONG") is Stance.AGAINST


def test_long_with_wait_verdict_is_neutral():
    assert position_stance("LONG", "WAIT", "WAIT") is Stance.NEUTRAL


def test_wait_final_but_opposite_raw_is_against_raw():
    """Ровно случай BTC/ETH: SHORT по графику погашен иерархией в WAIT."""
    assert position_stance("LONG", "WAIT", "SHORT") is Stance.AGAINST_RAW


def test_wait_final_with_supporting_raw_is_neutral():
    assert position_stance("LONG", "WAIT", "LONG") is Stance.NEUTRAL


def test_missing_raw_falls_back_to_final():
    assert position_stance("LONG", "WAIT", None) is Stance.NEUTRAL
    assert position_stance("LONG", "SHORT", None) is Stance.AGAINST


def test_unknown_verdict_is_neutral():
    assert position_stance("LONG", None, None) is Stance.NEUTRAL
    assert position_stance("LONG", "NODATA", None) is Stance.NEUTRAL


def test_side_is_case_insensitive():
    assert position_stance("long", "SHORT", "SHORT") is Stance.AGAINST


# ------------------------------------------------------- format_verdict_pair

def test_agreeing_pair_renders_once():
    out = format_verdict_pair("LONG", "LONG")
    assert out == "🟢 LONG"


def test_suppressed_raw_is_shown():
    out = format_verdict_pair("WAIT", "SHORT")
    assert "WAIT" in out
    assert "SHORT" in out
    assert "🔴" in out


def test_raw_none_renders_final_only():
    assert format_verdict_pair("WAIT", None) == "⚪ WAIT"


def test_regime_created_long_is_shown_too():
    """Обратный случай: сырой WAIT, финал LONG — режим создал сигнал."""
    out = format_verdict_pair("LONG", "WAIT")
    assert "LONG" in out and "WAIT" in out


# ------------------------------------------------------------ в отчёте

def test_position_against_system_is_marked_in_report():
    from src.daily_report import _render_orphan
    from src.matcher import MatchResult
    from src.portfolio import AggregatedPerpPosition

    pos = AggregatedPerpPosition(
        coin="NEAR", net_size=100.0, weighted_entry=1.64, total_pnl=10.0,
        contributors=[("main", 100.0)], avg_leverage=5.0,
        max_liquidation_distance_pct=25.0,
    )
    out = _render_orphan(
        [MatchResult(position=pos, decision=None, status="orphan")],
        {"NEAR": 1.7489},
        coin_verdicts={"NEAR": "SHORT"},
    )
    assert "ПРОТИВ СИСТЕМЫ" in out


def test_position_against_suppressed_raw_is_marked():
    from src.daily_report import _render_orphan
    from src.matcher import MatchResult
    from src.portfolio import AggregatedPerpPosition

    pos = AggregatedPerpPosition(
        coin="BTC", net_size=0.01, weighted_entry=80000.0, total_pnl=5.0,
        contributors=[("main", 0.01)], avg_leverage=3.0,
        max_liquidation_distance_pct=30.0,
    )
    out = _render_orphan(
        [MatchResult(position=pos, decision=None, status="orphan")],
        {"BTC": 81000.0},
        coin_verdicts={"BTC": "WAIT"},
        raw_verdicts={"BTC": "SHORT"},
    )
    assert "ГРАФИК ПРОТИВ" in out
    assert "SHORT" in out


def test_aligned_position_gets_no_marker():
    from src.daily_report import _render_orphan
    from src.matcher import MatchResult
    from src.portfolio import AggregatedPerpPosition

    pos = AggregatedPerpPosition(
        coin="NEAR", net_size=100.0, weighted_entry=1.64, total_pnl=10.0,
        contributors=[("main", 100.0)], avg_leverage=5.0,
        max_liquidation_distance_pct=25.0,
    )
    out = _render_orphan(
        [MatchResult(position=pos, decision=None, status="orphan")],
        {"NEAR": 1.7489},
        coin_verdicts={"NEAR": "LONG"},
    )
    assert "ПРОТИВ" not in out
