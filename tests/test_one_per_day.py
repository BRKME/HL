"""Три предложения в день (18.09.2026).

Указание оператора: «мы смотрим рынок в среднесрочной перспективе», затем
уточнение — оставить топ-3, а не одну позицию.

Одна была крайностью в другую сторону: порядок строится по НЕподтверждённой
оценке, и единственная строка делает цену ошибки ранжирования
максимальной. Три — компромисс между выбором и кашей.

Письмо 18.09 противоречило себе трижды разом: «вот семь входов», «это одна
бета-ставка на рынок», «счёт тянет одну сделку». Семь идей в день при
удержании неделями — две сотни идей на горизонте одной сделки.

Остальные входы НЕ прячутся: уходят в одну строку с монетами. Скрыть их
значило бы лишить оператора выбора, показать наравне — вернуть ту же кашу.
"""
import pytest

from src.digest_compact import keep_top_entry


def _v(coin, verdict="LONG"):
    return (coin, 1.0, verdict, "тренд вверх.", verdict, "тренд вверх.", None)


def test_top_three_remain():
    v = [_v("MORPHO"), _v("HYPE"), _v("TAO"), _v("BTC"), _v("ETH")]
    kept, line = keep_top_entry(
        v, scores={"HYPE": 0.9, "MORPHO": 0.5, "TAO": 0.3, "BTC": 0.1})
    assert sum(1 for x in kept if x[2] == "LONG") == 3
    assert [x[0] for x in kept[:3]] == ["HYPE", "MORPHO", "TAO"]


def test_others_are_named_not_hidden():
    """Оператор должен видеть, что ещё было, — иначе это не выбор."""
    v = [_v(c) for c in ("MORPHO", "HYPE", "TAO", "BTC", "ETH")]
    _, line = keep_top_entry(v, scores={"HYPE": 0.9, "MORPHO": 0.8,
                                        "TAO": 0.7})
    assert "BTC" in line and "ETH" in line
    assert "Ещё 2" in line


def test_few_entries_untouched():
    v = [_v("HYPE"), _v("TAO")]
    kept, line = keep_top_entry(v)
    assert kept == v
    assert line == ""


def test_waits_are_preserved():
    """Ожидающие и NODATA не трогаем: они не входы."""
    v = [_v(c) for c in ("HYPE", "TAO", "BTC", "ETH")]
    v += [_v("ZEC", "WAIT"), _v("ONDO", "NODATA")]
    kept, _ = keep_top_entry(v, scores={"HYPE": 1.0, "TAO": .9, "BTC": .8})
    assert any(x[0] == "ZEC" for x in kept)
    assert any(x[0] == "ONDO" for x in kept)


def test_without_scores_keeps_original_order():
    v = [_v(c) for c in ("MORPHO", "HYPE", "TAO", "BTC")]
    kept, _ = keep_top_entry(v)
    assert [x[0] for x in kept[:3]] == ["MORPHO", "HYPE", "TAO"]


def test_top_is_three_by_default():
    from src.digest_compact import TOP_ENTRIES

    assert TOP_ENTRIES == 3


def test_no_entries_at_all():
    v = [_v("ZEC", "WAIT")]
    kept, line = keep_top_entry(v)
    assert kept == v and line == ""
