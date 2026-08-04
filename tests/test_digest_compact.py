"""Сжатие дайджеста и китовой строки (03.08.2026).

Замер настоящего сообщения от 23:26: 33 строки, 1353 символа, из них 626 —
восемь одинаковых строк «НЕ ВХОДИТЬ». То есть 46% сообщения занимало
перечисление того, что ничего не происходит. Плюс китовая строка, где 6
монет из 8 показывали «—», и `regime BEAR` трижды в одном сообщении.

Правило, выведенное отсюда: перечисляй отклонения, а не норму. Оператору
нужно видеть, что монета проверена — но одной строкой на все восемь, а не
восемью строками с одинаковым вердиктом.

Полный список сохраняется, когда есть на что смотреть: если хоть по одной
монете есть вход, разворачиваются все, потому что тогда важно сравнение.
"""
from datetime import datetime, timezone

from src.digest_compact import (
    collapse_wait_verdicts,
    compact_stance_line,
)

NOW = datetime(2026, 8, 3, 20, 26, tzinfo=timezone.utc)


def _v(coin, verdict="WAIT", mark=1.0, rat="слабый сигнал"):
    return (coin, mark, verdict, rat, verdict, rat)


# ------------------------------------------------- collapse_wait_verdicts

def test_all_wait_collapses_to_one_line():
    verdicts = [_v(c) for c in ("BTC", "ETH", "ZEC", "NEAR",
                                "HYPE", "ASTER", "MORPHO", "TAO")]
    kept, summary = collapse_wait_verdicts(verdicts)
    assert kept == []
    assert summary is not None
    assert "8" in summary
    for c in ("BTC", "TAO"):
        assert c in summary


def test_any_entry_keeps_full_list():
    """Есть вход — разворачиваем всё: важно сравнение с остальными."""
    verdicts = [_v("BTC", "LONG")] + [_v(c) for c in ("ETH", "ZEC")]
    kept, summary = collapse_wait_verdicts(verdicts)
    assert len(kept) == 3
    assert summary is None


def test_short_entry_also_keeps_full_list():
    verdicts = [_v("BTC", "SHORT")] + [_v("ETH")]
    kept, summary = collapse_wait_verdicts(verdicts)
    assert len(kept) == 2
    assert summary is None


def test_nodata_is_reported_separately():
    """Нет данных — это отказ, а не «не входить». Прятать нельзя."""
    verdicts = [_v(c) for c in ("BTC", "ETH")] + [_v("TAO", "NODATA")]
    kept, summary = collapse_wait_verdicts(verdicts)
    assert len(kept) == 1
    assert kept[0][0] == "TAO"
    assert summary is not None
    assert "TAO" not in summary


def test_empty_input():
    kept, summary = collapse_wait_verdicts([])
    assert kept == []
    assert summary is None


def test_single_wait_still_collapses():
    kept, summary = collapse_wait_verdicts([_v("BTC")])
    assert kept == []
    assert "BTC" in summary


# --------------------------------------------------- compact_stance_line

def test_coins_without_stance_are_dropped():
    line = compact_stance_line("🐋 Киты 7d: BTC — • ETH 100%↓ • ZEC 100%↓ "
                               "• NEAR — • HYPE — • TAO —")
    assert "ETH" in line and "ZEC" in line
    assert "BTC" not in line
    assert "—" not in line


def test_line_with_no_data_at_all_disappears():
    assert compact_stance_line("🐋 Киты 7d: BTC — • ETH — • ZEC —") == ""


def test_line_without_prefix_is_returned_unchanged():
    assert compact_stance_line("что-то другое") == "что-то другое"


def test_empty_line_stays_empty():
    assert compact_stance_line("") == ""


def test_all_coins_with_stance_are_all_kept():
    line = compact_stance_line("🐋 Киты 7d: BTC 80%↑ • ETH 100%↓")
    assert "BTC" in line and "ETH" in line
