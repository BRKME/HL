"""Заголовок whale-дайджеста и гигиена буфера (08.08.2026).

Пришедшее сообщение: «Всего сигналов: 146», под ним один вход по HYPE.
Реконструкция буфера на момент отправки (`945d69e`):

    WHALE_DROP_OFF     128
    WHALE_NEW_ENTRANT   17
    WHALE_NEW_OPEN       1

То есть 145 из 146 — ротация лидерборда, которую сознательно не показывают
с 12.06: она не несёт торгового решения. Число честно описывало буфер и
врало читателю — обещало 146 событий, показывало одно.

Отсюда же вторая беда. `render_digest` возвращает None, когда показывать
нечего, и буфер в этом случае НЕ чистится. Ротация копилась днями (125 →
146 за неделю), а дайджест не уходил с 30 июля — девять дней при интервале
в сутки. Первый же настоящий сигнал вытащил наружу весь накопленный хвост
в виде числа в заголовке.

Правило: то, что решено не показывать, не кладётся в буфер показа.
"""
from datetime import datetime, timezone

from src.whale_report import DIGEST_HIDDEN_RULES, digest_visible, render_digest
from src.whale_monitor import Signal


NOW = datetime(2026, 8, 8, 4, 53, tzinfo=timezone.utc)


def _s(rule, coin="HYPE", msg="сигнал", **details):
    return Signal(rule=rule, coin=coin, severity=1, message=msg,
                  details=details or {"coin": coin})


# ------------------------------------------------------------ фильтрация

def test_rank_churn_is_not_digest_material():
    assert "WHALE_NEW_ENTRANT" in DIGEST_HIDDEN_RULES
    assert "WHALE_DROP_OFF" in DIGEST_HIDDEN_RULES


def test_visible_filter_drops_churn():
    sigs = ([_s("WHALE_DROP_OFF")] * 128 + [_s("WHALE_NEW_ENTRANT")] * 17
            + [_s("WHALE_NEW_OPEN")])
    assert len(digest_visible(sigs)) == 1


def test_visible_filter_keeps_overlap_and_new_open():
    sigs = [_s("WHALE_OVERLAP"), _s("WHALE_NEW_OPEN")]
    assert len(digest_visible(sigs)) == 2


def test_visible_filter_keeps_unknown_rules():
    """Неизвестное правило показывается: прятать молча — то же, что терять."""
    assert len(digest_visible([_s("WHALE_SOMETHING_NEW")])) == 1


def test_empty_input():
    assert digest_visible([]) == []


# -------------------------------------------------------------- заголовок

def test_headline_counts_only_what_is_shown():
    """Ровно случай 08.08: 146 в буфере, показать можно один."""
    sigs = ([_s("WHALE_DROP_OFF")] * 128 + [_s("WHALE_NEW_ENTRANT")] * 17
            + [_s("WHALE_NEW_OPEN", details={"coin": "HYPE"})])
    msg = render_digest(sigs, now=NOW)
    assert msg is not None
    assert "146" not in msg
    assert "Всего сигналов: 1" in msg


def test_digest_with_only_churn_is_not_sent():
    assert render_digest([_s("WHALE_DROP_OFF")] * 50, now=NOW) is None


def test_digest_with_nothing_is_not_sent():
    assert render_digest([], now=NOW) is None


def test_headline_matches_multiple_visible_signals():
    sigs = [_s("WHALE_NEW_OPEN"), _s("WHALE_OVERLAP")] + [_s("WHALE_DROP_OFF")] * 9
    msg = render_digest(sigs, now=NOW)
    assert "Всего сигналов: 2" in msg
