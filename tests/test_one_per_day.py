"""Одно предложение в день (18.09.2026).

Указание оператора: «предложение должно быть одно в день, ведь мы смотрим
рынок в среднесрочной перспективе».

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


def test_only_one_entry_remains():
    v = [_v("MORPHO"), _v("HYPE"), _v("TAO"), _v("BTC")]
    kept, line = keep_top_entry(v, scores={"HYPE": 0.9, "MORPHO": 0.5})
    assert sum(1 for x in kept if x[2] == "LONG") == 1
    assert kept[0][0] == "HYPE"


def test_others_are_named_not_hidden():
    """Оператор должен видеть, что ещё было, — иначе это не выбор."""
    v = [_v("MORPHO"), _v("HYPE"), _v("TAO")]
    _, line = keep_top_entry(v, scores={"HYPE": 0.9})
    assert "MORPHO" in line and "TAO" in line
    assert "Ещё 2" in line


def test_single_entry_untouched():
    v = [_v("HYPE")]
    kept, line = keep_top_entry(v)
    assert kept == v
    assert line == ""


def test_waits_are_preserved():
    """Ожидающие и NODATA не трогаем: они не входы."""
    v = [_v("HYPE"), _v("TAO"), _v("ZEC", "WAIT"), _v("ONDO", "NODATA")]
    kept, _ = keep_top_entry(v, scores={"HYPE": 1.0})
    assert any(x[0] == "ZEC" for x in kept)
    assert any(x[0] == "ONDO" for x in kept)


def test_without_scores_keeps_first():
    v = [_v("MORPHO"), _v("HYPE")]
    kept, _ = keep_top_entry(v)
    assert kept[0][0] == "MORPHO"


def test_no_entries_at_all():
    v = [_v("ZEC", "WAIT")]
    kept, line = keep_top_entry(v)
    assert kept == v and line == ""
