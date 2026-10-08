"""Смена режима — самое важное сообщение, оно должно читаться первым (08.10.2026).

Оператор: «такое важное сообщение надо как-то подсвечивать». Прежний текст —
«все открытые и планируемые позиции переоцениваются» — не говорил, что
делать, и выглядел как любое другое письмо. Telegram не даёт цвета, поэтому
выделение — сигнальная рамка и строка «что делать» из реального портфеля.
"""
from src.position_guard import format_regime_alert


def test_header_stands_out():
    msg = format_regime_alert("BULL", "BEAR", positions={})
    first = msg.splitlines()[0]
    assert first.startswith("🚨")
    assert "<b>СМЕНА РЕЖИМА: BULL → BEAR</b>" in first
    assert "━━━" in msg


def test_flat_says_nothing_to_do():
    msg = format_regime_alert("BULL", "BEAR", positions={})
    assert "Делать ничего не нужно" in msg
    assert "открытых позиций нет" in msg
    assert "Новые лонги система не даёт" in msg


def test_positions_to_close_are_named():
    msg = format_regime_alert("BULL", "BEAR",
                              positions={"BTC": "LONG", "MORPHO": "LONG"},
                              to_close=["BTC", "MORPHO"])
    assert "Закрыть: BTC LONG, MORPHO LONG" in msg
    assert "Делать ничего не нужно" not in msg


def test_untracked_position_against_regime_is_operator_call():
    """Ручная позиция без сигнала системы: политика выхода её не требует —
    называем, но решение за оператором."""
    msg = format_regime_alert("BULL", "BEAR", positions={"ETH": "LONG"},
                              to_close=[])
    assert "Против режима: ETH LONG" in msg
    assert "решение за тобой" in msg


def test_bull_forbids_shorts():
    msg = format_regime_alert("BEAR", "BULL", positions={})
    assert "Шорты система не даёт" in msg


def test_unknown_portfolio_is_not_called_flat():
    msg = format_regime_alert("BULL", "BEAR", positions=None)
    assert "Делать ничего не нужно" not in msg
    assert "проверь открытые позиции сам" in msg


def test_old_call_signature_still_works():
    msg = format_regime_alert("BULL", "BEAR")
    assert "СМЕНА РЕЖИМА" in msg
