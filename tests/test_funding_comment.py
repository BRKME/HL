"""Фандинг с человекочитаемым комментарием: кто кому платит + настроение рынка.
Оператор: голое число '+4.6% APR' непонятно."""
from src.tactical_signals import funding_comment


def test_positive_funding_short():
    # +фандинг: лонги платят шортам, рынок перекошен в лонги
    txt = funding_comment(4.6, "SHORT")
    assert "получаешь" in txt.lower() or "лонги платят" in txt.lower() or "тебе" in txt.lower()
    assert "лонг" in txt.lower()      # упоминание перекоса в лонги


def test_positive_funding_long():
    # +фандинг при LONG: ты платишь (минус для позиции)
    txt = funding_comment(4.6, "LONG")
    assert "платишь" in txt.lower()


def test_negative_funding_short():
    # -фандинг при SHORT: ты платишь шортам... нет, шорты платят лонгам
    txt = funding_comment(-3.0, "SHORT")
    assert "платишь" in txt.lower()


def test_negative_funding_long():
    # -фандинг при LONG: шорты платят лонгам, ты получаешь
    txt = funding_comment(-3.0, "LONG")
    assert "получаешь" in txt.lower() or "тебе" in txt.lower()
    assert "шорт" in txt.lower()


def test_none_funding():
    assert funding_comment(None, "SHORT") == ""


def test_near_zero_neutral():
    txt = funding_comment(0.2, "SHORT")
    assert "нейтрал" in txt.lower() or txt == "" or "близок" in txt.lower()
