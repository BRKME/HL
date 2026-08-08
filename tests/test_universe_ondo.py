"""ONDO добавлен в отслеживаемые (08.08.2026, решение оператора).

Вселенная задаётся двумя списками — `FOCUS_COINS` (вердикты, дайджест,
журнал, детектор покрытия) и `TACTICAL_COINS` (событийные сигналы). Настроек
по отдельным монетам в проекте нет, поэтому ONDO наследует всё остальное
автоматически: `TIER1 = {BTC, ETH}` делает его альтом, значит кап плеча 2×
по политике рисков.

Тест сторожит не сам факт присутствия ONDO, а то, что два списка не
разъедутся: пока они совпадают, монета не может попасть в тактику мимо
журнала или наоборот. Именно так ASTER однажды выпал из датасета целиком.
"""
from src.tactical_signals import TACTICAL_COINS
from src.whitelist_focus import FOCUS_COINS
from src.leverage import TIER1


def test_ondo_is_tracked_for_verdicts():
    assert "ONDO" in FOCUS_COINS


def test_ondo_is_tracked_for_tactical_signals():
    assert "ONDO" in TACTICAL_COINS


def test_both_universes_agree():
    """Разъезд списков однажды уже стоил целой монеты в датасете."""
    assert set(FOCUS_COINS) == set(TACTICAL_COINS)


def test_no_duplicates():
    assert len(FOCUS_COINS) == len(set(FOCUS_COINS))
    assert len(TACTICAL_COINS) == len(set(TACTICAL_COINS))


def test_ondo_is_an_alt_so_leverage_caps_at_2x():
    """Кап по классу — по исключению из TIER1, отдельной записи не нужно."""
    assert "ONDO" not in TIER1


def test_universe_size():
    assert len(FOCUS_COINS) == 9
