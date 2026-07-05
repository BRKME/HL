"""Ворота денег — пре-регистрация 05.07.2026 (решение кванта).

Реальный капитал включается на LONG-сторону ТОЛЬКО при: n>=15 закрытых
LONG-сигналов, avgR >= +0.4, медиана R > 0 (третье условие защищает от
одного жирного выброса, покупающего среднее). SHORT остаётся paper до
вердикта теневой выборки SHORT_RALLY_IN_BEAR. Алерт разовый (дедуп в
state). Автоторговли нет — советник советует, деньги включает оператор."""
import pytest
from src.money_gate import check_gate, GATE_N, GATE_AVG_R, format_gate_alert


def _rs(vals):
    return [{"direction": "LONG", "r_multiple": v} for v in vals]


def test_gate_closed_below_n():
    r = check_gate(_rs([0.5] * 14))
    assert r["open"] is False and r["n"] == 14


def test_gate_opens_on_solid_sample():
    r = check_gate(_rs([0.6, -1, 2, 0.8, -1, 1.5, 0.3, 2, -1, 0.9,
                        1.1, 0.4, -1, 2, 0.7]))
    assert r["n"] == 15 and r["open"] is True
    assert r["avg_r"] >= GATE_AVG_R and r["median_r"] > 0


def test_one_fat_outlier_cannot_buy_the_mean():
    """13 лузов по -0.3R + два выброса +9R: avgR=+0.94 >= порога, но медиана
    отрицательная — ворота ЗАКРЫТЫ. Ровно тот шум, от которого третье условие."""
    r = check_gate(_rs([-0.3] * 13 + [9.0, 9.0]))
    assert r["avg_r"] >= GATE_AVG_R
    assert r["open"] is False and r["median_r"] < 0


def test_shorts_ignored():
    rows = _rs([1.0] * 15) + [{"direction": "SHORT", "r_multiple": 5.0}] * 10
    r = check_gate(rows)
    assert r["n"] == 15  # SHORT в выборку ворот не входит


def test_unscored_rows_skipped():
    rows = _rs([1.0] * 15) + [{"direction": "LONG", "r_multiple": None}]
    assert check_gate(rows)["n"] == 15


def test_alert_text_carries_preregistered_sizing():
    r = check_gate(_rs([1.0] * 15))
    msg = format_gate_alert(r)
    assert "1%" in msg and "LONG" in msg and "SHORT" in msg.upper()
