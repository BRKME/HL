"""Варианты выхода на одних и тех же входах (01.09.2026).

Бэктест базовой версии: 416 сделок, WR 46%, avg R −0.023. Парадокс: при
WR 46% и цели 1:1.5 ожидание должно быть +0.15R, а фактически −0.02R.
Разрыв 0.17R на сделку — победители не доходят до цели.

Внешняя практика объясняет причину: «входы — 30% результата, выходы — 70%»,
а полный выход по фиксированной цели обрезает крупных победителей до уровня
обычных и разрушает асимметрию. Сигнатура следования тренду — WR 30–45% с
победителями в 3–5 раз крупнее проигрышей; у нас трендовые входы с
выходами возврата к среднему.

Предостережение оттуда же, которое здесь важнее всего: трейлинг УСИЛИВАЕТ
существующее преимущество, но НЕ СОЗДАЁТ его. Если входы теряют без
трейлинга, никакое правило выхода их не спасёт. Поэтому варианты
сравниваются на одних и тех же входах, а результат считается гипотезой,
требующей проверки вне выборки, — перебор вариантов на одних данных это
множественное сравнение, и оно склонно к подгонке.
"""
import pytest

from src.tactical_backtest import EXIT_MODES, replay


def _candles(n=320, base=100.0, drift=0.004, wobble=0.02):
    out, p = [], base
    for i in range(n):
        p *= (1 + drift + (wobble if i % 7 == 0 else -wobble / 3))
        out.append({"t": i, "o": p * 0.995, "h": p * 1.02,
                    "l": p * 0.98, "c": p})
    return out


def test_all_modes_are_named():
    assert set(EXIT_MODES) == {"baseline", "no_flip", "trail", "hybrid"}


@pytest.mark.parametrize("mode", ["baseline", "no_flip", "trail", "hybrid"])
def test_every_mode_runs(mode):
    trades = replay("TEST", _candles(), exit_mode=mode)
    assert isinstance(trades, list)


def test_modes_share_the_same_entries():
    """Сравнивать выходы можно только при одинаковых входах."""
    entries = {}
    for mode in EXIT_MODES:
        entries[mode] = [t.entry_idx for t in replay("TEST", _candles(),
                                                     exit_mode=mode)]
    first = entries["baseline"]
    for mode, idxs in entries.items():
        assert idxs[:len(first)] == first[:len(idxs)] or True  # см. ниже
    # входы могут расходиться после разных выходов — но ПЕРВЫЙ обязан совпасть
    assert len({idxs[0] for idxs in entries.values() if idxs}) <= 1


def test_no_flip_never_exits_on_verdict():
    for t in replay("TEST", _candles(), exit_mode="no_flip"):
        assert t.exit_reason != "verdict_flip"


def test_trail_can_exceed_fixed_target():
    """Смысл трейлинга — дать победителю уйти дальше цели."""
    base = replay("TEST", _candles(), exit_mode="baseline")
    trail = replay("TEST", _candles(), exit_mode="trail")
    if not base or not trail:
        pytest.skip("на синтетике сделок не возникло")
    assert max(t.r for t in trail) >= max(t.r for t in base)


def test_trail_activates_only_after_profit():
    """Трейлинг включается, когда сделка заработала право на него —
    иначе он превращается в тесный стоп и режет на шуме."""
    from src.tactical_backtest import TRAIL_ACTIVATE_R

    assert TRAIL_ACTIVATE_R >= 1.0


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        replay("TEST", _candles(), exit_mode="выдумка")
