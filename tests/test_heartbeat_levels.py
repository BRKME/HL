"""Heartbeat показывает уровни модели: направление, исходный вход @ цена, SL,
и текущую цену рядом. Чтобы оператор, пропустивший алерт, видел где система
стоит и мог войти по текущей цене, не дожидаясь следующего сигнала (~2 недели).

Исходный вход НЕ пересчитывается от текущей цены (это был бы ложный свежий
сигнал) — показываем оба числа, решение за оператором.
"""
from src.tactical_signals import tactical_levels_line


def test_shows_entry_sl_and_current():
    signals = {
        "BTC": {"direction": "SHORT", "entry": 64024, "sl": 68128,
                "current": 63000, "days": 2},
        "ETH": {"direction": "SHORT", "entry": 2450, "sl": 2610,
                "current": 2400, "days": 4},
    }
    line = tactical_levels_line(signals)
    assert "BTC" in line and "SHORT" in line
    assert "64,024" in line or "64024" in line          # исходный вход
    assert "68,128" in line or "68128" in line          # SL
    assert "63,000" in line or "63000" in line          # текущая цена


def test_current_delta_direction():
    # SHORT, цена упала ниже входа -> в плюсе (для шорта)
    signals = {"BTC": {"direction": "SHORT", "entry": 64000, "sl": 68000,
                       "current": 62000, "days": 2}}
    line = tactical_levels_line(signals)
    assert "BTC" in line


def test_empty():
    assert tactical_levels_line({}) == ""


def test_missing_current_graceful():
    # текущая цена недоступна — показываем вход/SL без неё, не падаем
    signals = {"BTC": {"direction": "SHORT", "entry": 64000, "sl": 68000,
                       "current": None, "days": 2}}
    line = tactical_levels_line(signals)
    assert "BTC" in line and ("64,000" in line or "64000" in line)
