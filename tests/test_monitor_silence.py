"""Портфельный дайджест шлётся ТОЛЬКО при реальных открытых позициях.

Оператор (13.06): без позиций сообщение «портфель $0 / доходность» — шум,
слать не надо ни в какой слот. Плюс защита от вчерашнего бага: упавший fetch
(все кошельки failed) НЕ должен выглядеть как «есть позиции» и слать $0.
"""
from src.daily_monitor import _should_send_report


def test_no_positions_any_slot_silent():
    # честно пустой портфель -> молчим в любой слот (утро больше не исключение)
    assert _should_send_report(has_perp=False, has_spot=False,
                               all_failed=False, hour=7, total_value=0.0) is False
    assert _should_send_report(has_perp=False, has_spot=False,
                               all_failed=False, hour=12, total_value=0.0) is False


def test_real_positions_send():
    assert _should_send_report(has_perp=True, has_spot=False,
                               all_failed=False, hour=12, total_value=1200.0) is True


def test_spot_positions_send():
    assert _should_send_report(has_perp=False, has_spot=True,
                               all_failed=False, hour=12, total_value=1200.0) is True


def test_failed_fetch_never_sends():
    # все кошельки упали -> данные недостоверны -> молчим (не шлём $0)
    assert _should_send_report(has_perp=True, has_spot=False,
                               all_failed=True, hour=12, total_value=0.0) is False
    assert _should_send_report(has_perp=False, has_spot=False,
                               all_failed=True, hour=7, total_value=0.0) is False


def test_zero_value_never_sends_even_with_phantom_positions():
    """Корень бага 13.06: $0-портфель приходил с фантомными позициями
    (has_perp True), но total_value=0 -> теперь молчим безусловно."""
    assert _should_send_report(has_perp=True, has_spot=True,
                               all_failed=False, hour=15,
                               total_value=0.0) is False


def test_real_value_with_positions_sends():
    assert _should_send_report(has_perp=True, has_spot=False,
                               all_failed=False, hour=15,
                               total_value=1500.0) is True
