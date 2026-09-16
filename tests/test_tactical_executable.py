"""Исполнимость размера в тактическом сигнале (16.09.2026).

Сигнал предлагал «размер ~8% депозита» при счёте $6 — это 48 центов при
минимальном ордере $10. Дайджест такую сделку уже помечал неисполнимой с
11.09, а тактический слой о размере счёта не знал вовсе: проверку я
добавил только в ОДНО из двух мест, где предлагается размер.

Тот же класс, что свечи в один построитель из двух и маркер в один
`git add` из двух — правка, применённая к одному вхождению.
"""
import pytest

from src.leverage import format_line

SUGGESTION = {"leverage": 1, "size_pct_equity": 8.0,
              "note": "риск 1% депозита при стопе 12.3%"}


def test_flags_unexecutable_size():
    """Ровно случай 16.09: 8% от $6 — сорок восемь центов."""
    out = format_line(SUGGESTION, equity=6)
    assert "нельзя" in out
    assert "0.48" in out


def test_shows_dollars_when_executable():
    out = format_line(SUGGESTION, equity=500)
    assert "$40" in out
    assert "нельзя" not in out


def test_without_equity_behaviour_unchanged():
    """Счёт недоступен — строка прежняя, а не исчезает: сигнал не должен
    зависеть от доступности биржи."""
    out = format_line(SUGGESTION)
    assert "размер ~8%" in out
    assert "$" not in out.split("размер")[1]


def test_threshold_matches_the_digest():
    """Порог обязан быть один: разные пороги в двух местах означали бы, что
    дайджест и сигнал спорят об одной и той же сделке."""
    from src.whitelist_focus import MIN_ORDER_USD

    just_below = format_line(SUGGESTION, equity=(MIN_ORDER_USD - 1) / 0.08)
    just_above = format_line(SUGGESTION, equity=(MIN_ORDER_USD + 1) / 0.08)
    assert "нельзя" in just_below
    assert "нельзя" not in just_above


def test_zero_equity_is_not_treated_as_unknown():
    """Ноль — не «счёт неизвестен»: пятый случай «ноль это ложь» был бы
    именно здесь."""
    assert "размер ~8%" in format_line(SUGGESTION, equity=0)
