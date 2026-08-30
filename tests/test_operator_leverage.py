"""Плечо оператора и запас до ликвидации (30.08.2026).

Оператор перешёл на 5x и попросил учесть это в настройках стопа.

Важное различие, которое надо держать в голове: **плечо не меняет, где
ставить стоп**. Стоп задаётся графиком — 2·ATR или ближайший swing-уровень
— и от плеча не зависит. Размер тоже: он считается от риска в 1% депозита и
дистанции стопа, а не от плеча.

Что плечо меняет по-настоящему — расстояние до ликвидации. При 5x она
примерно в 20% против позиции. Стоп шире этого НИКОГДА НЕ СРАБОТАЕТ:
позицию вынесет раньше, и убыток будет не запланированным 1%, а всей
маржой. Этой проверки в системе не было вовсе — при плечах 2-3x она почти
никогда не срабатывала бы, при 5x становится существенной.

Порог взят с запасом 20% от расстояния до ликвидации: биржа считает
поддерживающую маржу и комиссии, поэтому фактическая ликвидация ближе
номинальной.
"""
import pytest

from src.leverage import (
    OPERATOR_LEVERAGE,
    liquidation_distance_pct,
    stop_survives_liquidation,
)


def test_operator_leverage_is_five():
    assert OPERATOR_LEVERAGE == 5


@pytest.mark.parametrize("lev,expected", [(1, 100.0), (2, 50.0), (5, 20.0)])
def test_liquidation_distance(lev, expected):
    assert liquidation_distance_pct(lev) == pytest.approx(expected)


def test_narrow_stop_survives():
    assert stop_survives_liquidation(1.6, leverage=5) is True


def test_stop_near_liquidation_is_rejected():
    """12.3% при 5x проходит, 17% — уже нет: запас съеден."""
    assert stop_survives_liquidation(12.3, leverage=5) is True
    assert stop_survives_liquidation(17.0, leverage=5) is False


def test_stop_wider_than_liquidation_is_rejected():
    assert stop_survives_liquidation(25.0, leverage=5) is False


def test_same_stop_is_safe_at_lower_leverage():
    """Тот же стоп в 17% при 2x безопасен — дело в плече, не в стопе."""
    assert stop_survives_liquidation(17.0, leverage=2) is True


def test_degenerate_inputs():
    assert stop_survives_liquidation(0, leverage=5) is False
    assert stop_survives_liquidation(5.0, leverage=0) is False


def test_plan_line_refuses_stop_that_liquidates_first():
    """Печатать план, который не может исполниться, опаснее молчания."""
    from src.whitelist_focus import _plan_line

    # стоп 25% при 5x: ликвидация раньше
    assert _plan_line("LONG", entry=100.0, sl=75.0, n_entries=1) == ""


def test_plan_line_keeps_normal_stop():
    from src.whitelist_focus import _plan_line

    out = _plan_line("LONG", entry=100.0, sl=94.0, n_entries=1)
    assert "94" in out
