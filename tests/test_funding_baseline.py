"""Фандинг считается от базовой ставки Hyperliquid, а не от нуля (27.10.2026).

На HL фандинг = премия + процентная часть 0.01% за 8 ч = 10.95% годовых
при НУЛЕВОЙ премии. В журнале тактики 126 записей из 182 — ровно 10.95:
это обычное состояние рынка. Код считал нейтральным |APR| < 1% и писал
под каждым сигналом «рынок перекошен в лонги ⚠️ риск long squeeze» —
27.10 так были помечены ZEC и NEAR, а TAO-шорт 25.10 получил «силу:
высокую» отчасти за «попутный» фандинг, которого не было.

Кто кому платит — по знаку ставки (это деньги). Перекос — по отклонению
от базы (это настроение).
"""
from src.tactical_signals import (
    FUNDING_BASELINE_APR, funding_comment, signal_strength,
)


def test_baseline_is_hl_interest_rate():
    assert round(FUNDING_BASELINE_APR, 2) == 10.95


def test_baseline_funding_is_not_a_skew():
    for side in ("LONG", "SHORT"):
        txt = funding_comment(10.95, side)
        assert "перекос" not in txt or "перекоса нет" in txt
        assert "squeeze" not in txt
        assert "базов" in txt


def test_baseline_still_says_who_pays():
    assert "платишь" in funding_comment(10.95, "LONG")
    assert "получаешь" in funding_comment(10.95, "SHORT")


def test_well_above_baseline_is_long_skew():
    txt = funding_comment(40.0, "LONG")
    assert "перекошен в лонги" in txt and "squeeze" in txt


def test_below_baseline_is_short_skew_even_if_longs_pay():
    """4.46% (BTC 21.10): лонги всё ещё платят, но меньше базы — премия
    отрицательная, рынок клонится в шорты."""
    txt = funding_comment(4.46, "LONG")
    assert "лонги платят" in txt
    assert "перекошен в шорты" in txt


def test_strength_ignores_baseline_funding():
    """Фандинг на базовой ставке ни добавляет, ни снимает балл силы: до
    правки 10.95 давал шорту +1, лонгу −1 (ZEC и NEAR 27.10 — «средняя»)."""
    with_base = signal_strength("SHORT", "BEAR", 10.95, None)
    without = signal_strength("SHORT", "BEAR", None, None)
    assert with_base == without
    long_base = signal_strength("LONG", "BULL", 10.95, None)
    assert long_base == signal_strength("LONG", "BULL", None, None)
