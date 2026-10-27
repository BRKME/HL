"""Бэктест на топ-20 по объёму (27.10.2026, запрос оператора).

Предрегистрация — docs/OPERATING_POLICY.md, запись 27.10. Здесь — кирпичи:
выбор монет по ДОЛЛАРОВОМУ объёму и просадка в R.
"""
from src.tactical_backtest import max_drawdown_r, top_by_volume


def test_ranks_by_usd_not_base_volume():
    """dayBaseVlm в штуках: у PEPE миллиарды при скромных долларах."""
    meta = {"BTC": {"day_ntl_volume": 2e9, "day_volume": 2e4},
            "kPEPE": {"day_ntl_volume": 5e7, "day_volume": 9e12},
            "ETH": {"day_ntl_volume": 1e9, "day_volume": 4e5}}
    assert top_by_volume(meta, 2) == ["BTC", "ETH"]


def test_excludes_stables_delisted_and_missing_volume():
    meta = {"BTC": {"day_ntl_volume": 3e9},
            "USDE": {"day_ntl_volume": 9e9},
            "OLD": {"day_ntl_volume": 8e9, "delisted": True},
            "NOVOL": {"day_ntl_volume": None},
            "ETH": {"day_ntl_volume": 1e9}}
    assert top_by_volume(meta, 5) == ["BTC", "ETH"]


def test_drawdown_on_time_ordered_cumulative_r():
    # по времени: +1, −2, −1, +3 → пик 1, дно −2 → просадка 3
    pts = [(4, 3.0), (1, 1.0), (3, -1.0), (2, -2.0)]
    assert max_drawdown_r(pts) == 3.0


def test_drawdown_zero_when_only_gains():
    assert max_drawdown_r([(1, 0.5), (2, 1.0)]) == 0.0
    assert max_drawdown_r([]) == 0.0
