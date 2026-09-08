"""Исполнимость размера позиции (07.09.2026).

Проверка дайджеста от 07.09 при счёте $174: все предложенные размеры
оказались НИЖЕ минимального ордера биржи.

  HYPE   размер 1.4% = $2.44 нотионала
  NEAR   размер 1.0% = $1.74
  BTC    размер 2.5% = $4.35

Минимальный ордер на Hyperliquid около $10 — ни одну из этих сделок
открыть нельзя. В процентах это невидимо: «размер ~1.0%» выглядит
нормально, пока не переведёшь в деньги.

Причина: размер считается от риска 1% депозита ($1.74), делится на число
одновременных входов, и при семи входах остаются центы. Логика верна, но
результат неисполним — тот же класс, что стоп не на своей стороне.
"""
import pytest

from src.whitelist_focus import MIN_ORDER_USD, _plan_line


def test_size_removed_from_message():
    """Размер убран из письма по решению оператора 08.09: сколько брать —
    его выбор, зависящий от плеча. Расчёт ёмкости счёта остался."""
    out = _plan_line("LONG", entry=100.0, sl=90.0, n_entries=1, equity=1000.0)
    assert "размер" not in out
    assert "стоп" in out





def test_minimum_is_explicit():
    assert MIN_ORDER_USD == 10.0



def test_supportable_entries_small_account():
    """При $174 и стопе 10% счёт тянет ОДНУ сделку, а дайджест делил на 7."""
    from src.whitelist_focus import supportable_entries

    assert supportable_entries(174, stop_pct=10) == 1
    assert supportable_entries(174, stop_pct=6) == 2


def test_supportable_entries_grows_with_capital():
    from src.whitelist_focus import supportable_entries

    assert supportable_entries(1000, 10) == 10
    assert supportable_entries(5000, 10) == 50


def test_supportable_entries_degenerate():
    from src.whitelist_focus import supportable_entries

    assert supportable_entries(None, 10) == 0
    assert supportable_entries(174, 0) == 0





def test_capacity_still_computed_without_size_in_message():
    """Размер убран из письма, но ёмкость счёта считается: она про то,
    сколько сделок физически можно открыть, а не про размер каждой."""
    from src.whitelist_focus import supportable_entries

    assert supportable_entries(174, stop_pct=10) == 1
    assert supportable_entries(5000, stop_pct=10) == 50


def test_rs_hidden_for_btc():
    """«RS +0» у BTC — сравнение с самим собой, бессмысленно (08.09)."""
    import re
    import tempfile
    from datetime import datetime, timezone
    from pathlib import Path

    from src.whitelist_focus import render_whitelist_verdicts

    closes = [100.0 * (1.004 ** i) for i in range(220)]
    cd = {"BTC": {"mark": closes[-1], "candles_closes": closes,
                  "candles": [{"o": c, "h": c * 1.02, "l": c * 0.98, "c": c}
                              for c in closes]}}
    msg = re.sub(r"<[^>]+>", "", render_whitelist_verdicts(
        now=datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc), coin_data=cd,
        regime_snapshot=None, state_dir=Path(tempfile.mkdtemp()),
        show_whale_stance=False))
    assert "vs BTC" not in msg
