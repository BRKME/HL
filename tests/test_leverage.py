"""Рекомендация плеча — пре-регистрация 05.07.2026 (запрос оператора:
система должна предлагать плечо). Политика риск-первая: плечо не цель, а
следствие стопа. Капы: BTC/ETH 3x, альты 2x; выравнивание с режимом даёт
полный кап, TRANSITION/неизвестный — минус ступень; широкий стоп (>=6%)
режет вдвое (волатильный актив = меньше плеча, не больше); пол 1x.
Сайзинг: риск 1% депозита на сделку -> размер = 1% / дистанция стопа."""
import pytest
from src.leverage import suggest


def test_btc_long_in_bull_full_cap():
    r = suggest("BTC", "LONG", "BULL", entry=60000, sl=57600)  # стоп 4%
    assert r["leverage"] == 3
    assert r["size_pct_equity"] == pytest.approx(25.0)  # 1%/4%


def test_alt_capped_at_2x():
    r = suggest("ZEC", "SHORT", "BEAR", entry=100, sl=104)
    assert r["leverage"] == 2


def test_transition_steps_down():
    r = suggest("BTC", "LONG", "TRANSITION", entry=60000, sl=57600)
    assert r["leverage"] == 2
    r2 = suggest("TAO", "LONG", None, entry=300, sl=288)
    assert r2["leverage"] == 1


def test_wide_stop_halves_leverage():
    """Стоп 8% = волатильный вход: BULL-кап BTC 3x -> 1x (3//2=1.5->1)."""
    r = suggest("BTC", "LONG", "BULL", entry=60000, sl=55200)
    assert r["leverage"] == 1
    assert r["size_pct_equity"] == pytest.approx(12.5)  # 1%/8%


def test_counter_regime_floors_at_1x():
    r = suggest("ETH", "SHORT", "BULL", entry=1700, sl=1768)
    assert r["leverage"] == 1


def test_size_capped_at_50pct():
    """Стоп 1% дал бы размер 100% депозита — кап 50% (риск concentr.)."""
    r = suggest("BTC", "LONG", "BULL", entry=60000, sl=59400)
    assert r["size_pct_equity"] == 50.0


def test_degenerate_inputs_safe():
    assert suggest("BTC", "LONG", "BULL", entry=0, sl=0) is None
    assert suggest("BTC", "LONG", "BULL", entry=100, sl=100) is None
