"""Тесты src/tactical_eval.py — оценка исходов тактических сигналов.

Правила исхода (first-touch по дневным свечам, горизонт 7д):
  LONG: low <= SL раньше -> −1R; high >= entry+2R раньше -> +2R;
  обе границы в ОДНОЙ свече -> консервативно −1R (порядок внутри дня неизвестен);
  ни та ни другая за 7д -> частичный R = (close7 − entry)/R, статус open_expired.
SHORT — зеркально. KPI-агрегат: n < 5 -> «рано судить», цифры всегда с n.
"""
from __future__ import annotations

from src import tactical_eval as te


def _c(h, l, c):
    return {"h": h, "l": l, "c": c}


class TestLongOutcomes:
    # entry 100, SL 95 -> R=5, цель 110
    def test_sl_hit_first(self):
        candles = [_c(105, 94, 96), _c(112, 96, 111)]
        r = te.evaluate_signal("LONG", entry=100, sl=95, candles=candles)
        assert r["r_multiple"] == -1.0
        assert r["status"] == "loss"

    def test_target_hit_first(self):
        candles = [_c(111, 98, 110)]
        r = te.evaluate_signal("LONG", entry=100, sl=95, candles=candles)
        assert r["r_multiple"] == 2.0
        assert r["status"] == "win"

    def test_same_candle_ambiguity_is_loss(self):
        candles = [_c(115, 90, 100)]
        r = te.evaluate_signal("LONG", entry=100, sl=95, candles=candles)
        assert r["status"] == "loss"      # консервативно

    def test_neither_partial_r(self):
        candles = [_c(104, 97, 103)] * 7
        r = te.evaluate_signal("LONG", entry=100, sl=95, candles=candles)
        assert r["status"] == "open_expired"
        assert abs(r["r_multiple"] - 0.6) < 1e-9     # (103-100)/5

    def test_not_enough_candles_pending(self):
        r = te.evaluate_signal("LONG", entry=100, sl=95, candles=[_c(101, 99, 100)])
        assert r["status"] == "pending"


class TestShortOutcomes:
    # entry 100, SL 105 -> R=5, цель 90
    def test_short_sl_hit(self):
        candles = [_c(106, 98, 99)]
        r = te.evaluate_signal("SHORT", entry=100, sl=105, candles=candles)
        assert r["r_multiple"] == -1.0

    def test_short_target_hit(self):
        candles = [_c(101, 89, 91)]
        r = te.evaluate_signal("SHORT", entry=100, sl=105, candles=candles)
        assert r["r_multiple"] == 2.0


class TestAggregate:
    def test_too_few_is_early(self):
        rows = [{"status": "win", "r_multiple": 2.0}] * 3
        s = te.aggregate(rows)
        assert s["verdict_ready"] is False
        assert "рано" in s["line"]

    def test_aggregate_line(self):
        rows = ([{"status": "win", "r_multiple": 2.0, "direction": "LONG"}] * 4
                + [{"status": "loss", "r_multiple": -1.0, "direction": "LONG"}] * 2
                + [{"status": "open_expired", "r_multiple": 0.5, "direction": "SHORT"}])
        s = te.aggregate(rows)
        # закрытых 6: WR 67%; средний R по всем оценённым 7
        assert "n=6" in s["line"]
        assert "WR 67%" in s["line"]
        assert "R" in s["line"]
        assert s["verdict_ready"] is True

    def test_pending_excluded(self):
        rows = [{"status": "pending", "r_multiple": None}] * 10
        s = te.aggregate(rows)
        assert "рано" in s["line"]
