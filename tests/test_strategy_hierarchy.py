"""Инвариант иерархии таймфреймов: тактика никогда не торгует против стратегии.

Правило оператора: OracAI — стратегия (месяцы), planner — тактика (день-неделя).
Если стратегический режим BULL — тактический вердикт НИКОГДА не SHORT.
«Вершинность» при бычьем режиме выражается как фиксация прибыли и WAIT,
а не как разворотная ставка против макро-дрифта с неограниченным риском.

Асимметрия сознательная: зеркального запрета «BEAR → не LONG» нет —
капитуляционные лонги на дне остаются (согласовано с философией лестницы).

Цена правила измерима: raw-vs-final A/B журнал покажет, сколько WR стоили
или сэкономили запрещённые шорты.
"""
from __future__ import annotations

from src.eth_focus import _compute_verdict, enforce_hierarchy


class TestBullNeverShort:
    def test_euphoria_overheated_in_bull_is_wait_not_short(self):
        # раньше это давало SHORT — нарушение иерархии
        ta = {"above_ema50": True, "above_ema200": True, "rsi_d1": 78}
        v, r = _compute_verdict(ta=ta, funding_apr_pct=20,
                                whale_net_long=None, whale_cluster_count=0,
                                regime="BULL", phase="EUPHORIA")
        assert v != "SHORT"
        assert v == "WAIT"
        # рационал объясняет: фиксируй, шорт запрещён стратегией
        low = r.lower()
        assert "фиксируй" in low or "иерарх" in low or "bull" in low

    def test_late_bull_overheated_is_wait_not_short(self):
        ta = {"above_ema50": True, "above_ema200": True,
              "rsi_d1": 74, "last": 2480, "swing_low": 2000, "swing_high": 2500}
        v, _ = _compute_verdict(ta=ta, funding_apr_pct=18,
                                whale_net_long=None, whale_cluster_count=0,
                                regime="BULL", phase="LATE_BULL")
        assert v != "SHORT"

    def test_raw_downtrend_in_bull_stays_blocked(self):
        # существующий блокер: сырой SHORT при BULL → WAIT (регресс-защита)
        ta = {"above_ema50": False, "above_ema200": False,
              "rsi_d1": 50, "last": 2000, "swing_low": 1900, "swing_high": 2500}
        v, _ = _compute_verdict(ta=ta, funding_apr_pct=None,
                                whale_net_long=None, whale_cluster_count=0,
                                regime="BULL", phase="MID_BULL")
        assert v != "SHORT"


class TestContrarianPreservedOutsideBull:
    def test_transition_euphoria_overheated_still_shorts(self):
        # стратегия уже НЕ bull → контрарианский шорт у вершины легитимен
        ta = {"above_ema50": True, "above_ema200": True, "rsi_d1": 78}
        v, _ = _compute_verdict(ta=ta, funding_apr_pct=20,
                                whale_net_long=None, whale_cluster_count=0,
                                regime="TRANSITION", phase="EUPHORIA")
        assert v == "SHORT"

    def test_no_regime_info_top_overheated_shorts(self):
        # нет данных о стратегии → нельзя утверждать BULL → тактика действует
        ta = {"above_ema50": True, "above_ema200": True, "rsi_d1": 78}
        v, _ = _compute_verdict(ta=ta, funding_apr_pct=20,
                                whale_net_long=None, whale_cluster_count=0,
                                regime=None, phase="EUPHORIA")
        assert v == "SHORT"

    def test_capitulation_long_in_bear_unchanged(self):
        # асимметрия: капитуляционный лонг на дне НЕ запрещён
        ta = {"above_ema50": False, "above_ema200": False,
              "rsi_d1": 25, "last": 1700, "swing_low": 1690, "swing_high": 2500}
        v, _ = _compute_verdict(ta=ta, funding_apr_pct=-12,
                                whale_net_long=None, whale_cluster_count=0,
                                regime="BEAR", phase="CAPITULATION")
        assert v == "LONG"


class TestHardGate:
    """enforce_hierarchy — последний рубеж: ловит ЛЮБОЙ будущий путь к SHORT
    при BULL, даже если кто-то добавит ветку выше и забудет про правило."""

    def test_gate_flips_short_to_wait_in_bull(self):
        v, r = enforce_hierarchy("SHORT", "rationale", regime="BULL")
        assert v == "WAIT"
        assert "bull" in r.lower() or "иерарх" in r.lower()

    def test_gate_passes_short_outside_bull(self):
        assert enforce_hierarchy("SHORT", "x", regime="BEAR")[0] == "SHORT"
        assert enforce_hierarchy("SHORT", "x", regime=None)[0] == "SHORT"

    def test_gate_never_touches_long_or_wait(self):
        assert enforce_hierarchy("LONG", "x", regime="BULL")[0] == "LONG"
        assert enforce_hierarchy("WAIT", "x", regime="BULL")[0] == "WAIT"


class TestPlaybookText:
    def test_bull_euphoria_playbook_has_no_short_advice(self):
        from src.daily_report import _REGIME_ADVICE
        text = _REGIME_ADVICE[("BULL", "EUPHORIA")].lower()
        assert "шорт" not in text and "short" not in text
