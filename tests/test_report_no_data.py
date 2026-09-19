"""Отсутствие данных ≠ нулевой результат (18.09.2026).

Письмо 18.09 показывало «7d WR 0% avg +0.0%» у шести групп из девяти. Это
читалось как «провалилось в 100% случаев», а означало «семь дней ещё не
прошло»: агрегация писала 0.0 при пустом списке доходностей, и отличить
ноль от отсутствия становилось невозможно.

Пятый случай «ноль это ложь» за две недели — до него метка времени 0
отбрасывалась в свече, в заполнении, в индексе китов и в ёмкости счёта.

Заодно главный горизонт переведён на НЕДЕЛЬНЫЙ: метка 🎯 по суточному
винрейту стояла у ZEC с 24ч +6.7% при 7д −2.8%, то есть у группы,
проигрышной там, где мы торгуем.
"""
import pytest

from src.signal_backtester import (
    MIN_EVENTS_ACTIONABLE, PRIMARY_HORIZON, BacktestGroup,
)


def _group(n=12, wr24=0.75, wr168=None, av24=3.0, av168=None):
    wr = {24: wr24}
    av = {24: av24}
    if wr168 is not None:
        wr[168] = wr168
        av[168] = av168 if av168 is not None else 0.0
    return BacktestGroup(coin="ZEC", rule="WHALE_NEW_OPEN", direction="long",
                         n_events=n, win_rate=wr, avg_return_pct=av,
                         max_dd_pct={24: -2.0})


def test_primary_horizon_is_weekly():
    """Мы торгуем неделями с 16.09 — метка обязана мерить их."""
    assert PRIMARY_HORIZON == 168


def test_no_weekly_data_means_no_marker():
    """Отсутствие оценки не равно провалу, но основанием для метки быть
    не может."""
    assert _group(wr168=None).is_actionable() is False


def test_weekly_loser_is_not_actionable():
    """Ровно случай ZEC 18.09: суточный винрейт 69%, недельный 31%."""
    assert _group(wr24=0.69, wr168=0.31, av24=6.7, av168=-2.8).is_actionable() is False


def test_weekly_winner_is_actionable():
    assert _group(wr24=0.75, wr168=0.83, av168=5.4).is_actionable() is True


def test_small_sample_is_not_actionable():
    assert _group(n=MIN_EVENTS_ACTIONABLE - 1, wr168=0.9).is_actionable() is False


def test_missing_horizon_is_absent_not_zero():
    """Ключевое: при пустых данных ключ ОТСУТСТВУЕТ, а не равен нулю."""
    from src.signal_backtester import _aggregate, SignalOutcome

    o = SignalOutcome(signal=None, direction="long", entry_price=1.0,
                      returns_pct={24: 1.5})
    wr, avg, _dd = _aggregate([o])
    assert 24 in wr
    assert 168 not in wr, "отсутствие данных не должно превращаться в 0.0"


def test_report_says_not_ripe_instead_of_zero():
    import re
    from datetime import datetime, timezone

    from src.signal_backtester import render_report

    text = re.sub(r"<[^>]+>", "", render_report(
        [_group(wr168=None)], now=datetime(2026, 9, 18, tzinfo=timezone.utc)))
    assert "не созрело" in text
    assert "7д: WR 0%" not in text
