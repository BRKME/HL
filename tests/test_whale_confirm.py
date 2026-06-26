"""Киты подтверждают тренд внутри сигнала — но WR/N показываем ТОЛЬКО когда
выборка зрела (N≥10). На незрелой — без процентов, честное 'рано судить'.
Решение аналитика: не плодить ложную уверенность идеальным WR на малой выборке.
"""
from src.tactical_signals import whale_confirmation


def test_mature_sample_shows_wr():
    # N≥10 и хороший WR -> подтверждение с цифрами
    txt = whale_confirmation(direction="SHORT", n_events=40, wr=1.0)
    assert "подтвержд" in txt.lower()
    assert "40" in txt
    assert "100%" in txt


def test_immature_sample_no_percentages():
    # N<10 -> без WR, честная пометка
    txt = whale_confirmation(direction="SHORT", n_events=4, wr=1.0)
    assert "100%" not in txt
    assert "рано" in txt.lower() or "зреет" in txt.lower() or "мало" in txt.lower()
    assert "4" in txt


def test_mature_but_weak_wr_flagged():
    # N≥10 но WR ниже порога -> не подтверждает
    txt = whale_confirmation(direction="SHORT", n_events=20, wr=0.45)
    assert "не подтвержд" in txt.lower() or "противореч" in txt.lower() or "слаб" in txt.lower()


def test_no_data():
    txt = whale_confirmation(direction="SHORT", n_events=0, wr=None)
    assert "нет данных" in txt.lower() or "n/a" in txt.lower()
