"""Сила сигнала: согласованность тренда, режима, фандинга и китов. Чтобы не все
сигналы выглядели одинаково весомо — оператор видит, сильный сетап или
пограничный."""
from src.tactical_signals import signal_strength


def test_aligned_factors_strong():
    # тренд вниз + режим BEAR + фандинг подтверждает + киты согласны -> сильный
    s = signal_strength(direction="SHORT", regime="BEAR",
                        funding_apr_pct=3.0, whale_aligned=True)
    assert s in ("высокая", "сильная")


def test_funding_against_weakens():
    # фандинг ПРОТИВ (шорт в перешортованном рынке) -> слабее
    s_against = signal_strength(direction="SHORT", regime="BEAR",
                               funding_apr_pct=-6.0, whale_aligned=None)
    s_for = signal_strength(direction="SHORT", regime="BEAR",
                           funding_apr_pct=3.0, whale_aligned=None)
    order = ["низкая", "средняя", "высокая"]
    assert order.index(s_against) < order.index(s_for)


def test_regime_conflict_weak():
    # режим против направления -> низкая
    s = signal_strength(direction="LONG", regime="BEAR",
                       funding_apr_pct=0.0, whale_aligned=False)
    assert s == "низкая"


def test_returns_valid_label():
    s = signal_strength(direction="SHORT", regime="BEAR",
                       funding_apr_pct=None, whale_aligned=None)
    assert s in ("низкая", "средняя", "высокая")
