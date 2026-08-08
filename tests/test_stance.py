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

from src.stance import Stance, position_stance, format_position_verdict


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


# --------------------------------------------------- format_position_verdict

def test_no_conflict_renders_status_only():
    assert format_position_verdict("LONG", "LONG", "LONG") == "🟢 LONG"


def test_layer_divergence_alone_is_not_shown():
    """Слои разошлись, но против позиции никто — лишнего не печатаем."""
    assert format_position_verdict("LONG", "LONG", "WAIT") == "🟢 LONG"


def test_silent_verdict_renders_status_only():
    assert format_position_verdict("LONG", "WAIT", "LONG") == "⚪ WAIT"


def test_verdict_against_position_recommends_closing():
    out = format_position_verdict("LONG", "SHORT", "SHORT")
    assert out == "🔴 SHORT | Система рекомендует закрыть LONG"


def test_short_position_against_long_verdict():
    out = format_position_verdict("SHORT", "LONG", "LONG")
    assert out == "🟢 LONG | Система рекомендует закрыть SHORT"


def test_suppressed_chart_signal_recommends_tightening_stop():
    """Сигнал был погашен режимом — рекомендация из самого инварианта."""
    out = format_position_verdict("LONG", "WAIT", "SHORT")
    assert out.startswith("⚪ WAIT | Система рекомендует подтянуть стоп")
    assert "вниз" in out


def test_suppressed_chart_signal_for_short_position():
    out = format_position_verdict("SHORT", "WAIT", "LONG")
    assert "подтянуть стоп" in out
    assert "вверх" in out


def test_empty_verdict_renders_nothing():
    assert format_position_verdict("LONG", None, None) == ""


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
    assert "Система рекомендует закрыть" in out


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
    assert "Система рекомендует подтянуть стоп" in out
    assert "вниз" in out


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
    assert "Система рекомендует" not in out


def test_calm_line_survives_pending_exit_of_other_side():
    """08.08: висящий exit по BTC SHORT не должен глушить отчёт при лонге.

    «ЗАКРОЙ» рисуется только при совпадении closed_direction со стороной
    позиции — условие гашения обязано совпадать с условием печати."""
    import src.daily_report as dr
    from datetime import datetime, timezone
    from src.matcher import MatchResult
    from src.portfolio import AggregatedPerpPosition

    pos = AggregatedPerpPosition(
        coin="BTC", net_size=0.01, weighted_entry=80000.0, total_pnl=5.0,
        contributors=[("main", 0.01)], avg_leverage=3.0,
        max_liquidation_distance_pct=30.0,
    )
    text = "\n".join(dr.render_daily_report(
        [MatchResult(position=pos, decision=None, status="orphan")],
        [], {"BTC": 81000.0}, None, 1000.0,
        now=datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc),
    ))
    assert "ЗАКРОЙ" not in text
    assert "Без алертов" in text
