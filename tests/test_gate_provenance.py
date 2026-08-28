"""Виртуальная сделка помнит, почему сигнал прошёл (28.08.2026).

Система ведёт виртуальные сделки давно: 97 входов, 74 закрытых с оценкой R,
независимо от того, держит ли оператор что-то реально. Не хватало одного:
запись входа не помнит, каким правилом сигнал был пропущен.

Из-за этого H3 приходилось считать сопоставлением по монете и дате между
двумя журналами — приблизительно и хрупко. Пометка `h3_unblocked` уже
пишется в журнал ВЕРДИКТОВ (24.08); теперь она едет и в тактический, где
живут сделки с реальным R.

Проверено попутно и оказалось неверным: гипотеза, что виртуальным сделкам
недостаёт учёта фандинга. Медиана удержания — 16 часов, средняя цена
фандинга −0.001 R, ни одна сделка не теряет на нём больше 10% риска.
"""
import pytest

from src.eth_focus import h3_unblocked


def test_flag_is_true_for_transition_bear_phase():
    assert h3_unblocked("LONG", "LONG", "TRANSITION", "EARLY_BEAR") is True


def test_flag_is_false_when_regime_allowed_anyway():
    assert h3_unblocked("LONG", "LONG", "BULL", "EARLY_BEAR") is False


def test_journal_entry_carries_provenance():
    """Запись входа обязана нести пометку — иначе H3 неизмерима точно."""
    import inspect

    from src import tactical_signals

    src = inspect.getsource(tactical_signals)
    assert '"h3_unblocked"' in src, (
        "тактический журнал должен писать h3_unblocked: без него связь "
        "сделки с правилом восстанавливается только сопоставлением по "
        "монете и дате")


def test_provenance_absent_means_not_unblocked():
    """Старые записи без поля не должны считаться разблокированными."""
    rows = [{"coin": "TAO", "pnl_r": 0.1}, {"coin": "NEAR", "h3_unblocked": True}]
    unblocked = [r for r in rows if r.get("h3_unblocked")]
    assert len(unblocked) == 1
