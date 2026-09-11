"""Приоритет входов в дайджесте (11.09.2026).

Запрос оператора: сверху самая интересная монета. Веса взяты из
ФАКТИЧЕСКИХ замеров, а не назначены:

  расстановка сил  0.45 — «крупные в шорте при толпе в лонге» дало −0.45%
                          на семи монетах из девяти (07–08.09);
  сила против BTC  0.13 — разница avg R между RS>0 и RS<0 = +0.128 (H4);
  новизна          0.10 — НЕ измерялась, вес мал намеренно.

Ни один признак не перешёл порог значимости: RS дал +0.128 при пороге
0.20, расстановка −0.45% при пороге 0.5%. Порядок — подсказка, а не
сигнал: он меняет, что читать первым, и не меняет, что эмитируется.
"""
import pytest

from src.digest_compact import entry_score


def test_unexecutable_sinks_to_the_bottom():
    """Сделка, которую нельзя открыть, не может быть первой — каким бы
    хорошим ни был сигнал."""
    great = entry_score(rs_pp=100, accounts_ratio=0.5, top_pos_ratio=3.0,
                        is_new=True, executable=False)
    plain = entry_score(rs_pp=0, accounts_ratio=None, top_pos_ratio=None,
                        is_new=False)
    assert great < plain


def test_big_money_against_crowd_lowers_score():
    """Единственный признак с устойчивым знаком — он и весит больше всех."""
    against = entry_score(10, accounts_ratio=3.0, top_pos_ratio=0.6,
                          is_new=False)
    aligned = entry_score(10, accounts_ratio=3.0, top_pos_ratio=3.0,
                          is_new=False)
    assert against < aligned


def test_positioning_outweighs_relative_strength():
    """Расстановка измерена сильнее, чем RS, — и весит соответственно."""
    strong_rs_bad_pos = entry_score(30, accounts_ratio=3.0,
                                    top_pos_ratio=0.5, is_new=False)
    weak_rs_good_pos = entry_score(0, accounts_ratio=0.5,
                                   top_pos_ratio=3.0, is_new=False)
    assert weak_rs_good_pos > strong_rs_bad_pos


def test_relative_strength_is_capped():
    """+105 п.п. у ZEC не втрое лучше, чем +35: это уже область, где рост
    говорит скорее о перегреве, чем о качестве."""
    assert entry_score(105, None, None, False) == pytest.approx(
        entry_score(30, None, None, False))


def test_novelty_breaks_ties_but_does_not_dominate():
    new = entry_score(0, None, None, is_new=True)
    old_but_strong = entry_score(30, None, None, is_new=False)
    assert new > 0
    assert old_but_strong > new


def test_missing_data_is_neutral():
    assert entry_score(None, None, None, False) == 0.0


def test_score_is_ordered_not_absolute():
    """Оценка нужна для порядка; её величина смысла не имеет."""
    a = entry_score(20, 2.0, 2.2, True)
    b = entry_score(5, 2.0, 1.0, False)
    assert a > b
