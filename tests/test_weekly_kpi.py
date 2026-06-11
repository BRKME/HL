"""Тесты src/weekly_kpi.py — воскресная KPI-сводка HL-проекта.

Слои: тактика (R-мультипли из tactical_journal), субботний советник
(SKIP/EXIT медведей против always-DCA по BTC-прокси), портфель (неделя P&L).
Ограждения честности: n рядом с каждой цифрой, «рано судить» ниже порогов.
"""
from __future__ import annotations

from src import weekly_kpi as wk


class TestAdvisorAlpha:
    def _decisions(self):
        # 4 субботы: BUY в росте (+5%), SKIP в падении (-10%) — советник прав
        # дважды; BUY в падении (-3%) и SKIP в росте (+4%) — дважды неправ.
        return [
            {"signal": "STRONG", "btc_fwd7": 0.05},
            {"signal": "SKIP", "btc_fwd7": -0.10},
            {"signal": "MODERATE", "btc_fwd7": -0.03},
            {"signal": "EXIT", "btc_fwd7": 0.04},
        ]

    def test_alpha_math(self):
        res = wk.advisor_alpha(self._decisions())
        # планнер: +5, 0, −3, 0 = +2%; always: +5−10−3+4 = −4%; альфа = +6пп
        assert abs(res["planner_ret"] - 0.02) < 1e-9
        assert abs(res["always_ret"] - (-0.04)) < 1e-9
        assert abs(res["alpha_pp"] - 6.0) < 1e-9
        assert res["n_weeks"] == 4

    def test_alpha_line_has_n(self):
        res = wk.advisor_alpha(self._decisions())
        assert "n=4" in res["line"]
        assert "+6.0пп" in res["line"]

    def test_too_few_weeks_early(self):
        res = wk.advisor_alpha(self._decisions()[:2])
        assert "рано" in res["line"]


class TestMessage:
    def test_message_assembles_available_blocks(self):
        msg = wk.build_message(
            tactical_line="Тактика: n=6 закрытых · WR 67% · avg R +0.85",
            advisor_line="Суббота: альфа +6.0пп vs always-DCA (n=4 недель)",
            portfolio_line="Портфель: неделя −25.2% (BTC −18.0%)",
        )
        assert "Тактика" in msg and "Суббота" in msg and "Портфель" in msg
        assert "KPI" in msg

    def test_missing_blocks_skipped(self):
        msg = wk.build_message(tactical_line="Тактика: рано судить",
                               advisor_line=None, portfolio_line=None)
        assert "Суббота" not in msg and "Портфель" not in msg
