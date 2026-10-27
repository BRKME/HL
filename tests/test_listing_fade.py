"""Шорт новых листингов Hyperliquid со второго дня (27.10.2026).

Предрегистрация — docs/OPERATING_POLICY.md, запись 27.10. Основание —
событийные исследования спотовых листингов 2019–2021: рост до и в день
листинга, отрицательный дрейф со второго дня. Для перпов HL не проверено.
"""
from src.listing_fade import (
    listing_index, short_return, split_halves_edge,
)


def _c(o, h, c):
    return {"o": o, "h": h, "c": c}


def test_listing_detected_only_inside_window():
    day = 86_400_000
    start = 1_000 * day
    inside = [{"t": start + 40 * day}, {"t": start + 41 * day}]
    assert listing_index(inside, window_start_ms=start) == 0
    old = [{"t": start + 1 * day}, {"t": start + 2 * day}]
    assert listing_index(old, window_start_ms=start) is None   # жил до окна


def test_short_profit_when_price_falls():
    cs = [_c(10, 11, 10), _c(10, 10, 10), _c(10, 10, 9), _c(9, 9, 8)]
    r, stopped = short_return(cs, entry_idx=1, horizon=2, stop_pct=None)
    assert round(r, 6) == 20.0 and not stopped


def test_stop_by_intraday_high():
    cs = [_c(10, 10, 10), _c(10, 10, 10), _c(10, 12.5, 9)]
    r, stopped = short_return(cs, entry_idx=1, horizon=1, stop_pct=20)
    assert r == -20.0 and stopped


def test_gap_through_stop_costs_more_than_stop():
    """Открытие выше стопа: выход по открытию, убыток больше заявленного —
    иначе хвост сквиза занижен (JELLYJELLY)."""
    cs = [_c(10, 10, 10), _c(10, 10, 10), _c(15, 16, 14)]
    r, stopped = short_return(cs, entry_idx=1, horizon=1, stop_pct=20)
    assert r == -50.0 and stopped


def test_not_enough_days_returns_none():
    cs = [_c(10, 10, 10), _c(10, 10, 10)]
    assert short_return(cs, entry_idx=1, horizon=3, stop_pct=None) is None


def test_halves_edge_both_must_be_checked():
    # (время, доходность события, контроль)
    ev = [(1, 5.0), (2, 6.0), (3, -4.0), (4, -5.0)]
    lo, hi = split_halves_edge(ev, control_avg=0.0)
    assert lo > 0 and hi < 0
