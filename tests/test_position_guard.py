"""Position guard — пре-регистрация 03.07.2026 после кейса BTC/ETH шортов:
ETH ушёл за SL на 3+ часа без алерта, BTC держался против режимного флипа
TRANSITION->BULL. Политика выхода отслеживаемых позиций, зафиксированная
кодом ДО следующих сделок: sl_breach -> tp_hit -> verdict_flip -> regime_flip
(первое совпадение побеждает). Гвард только сторожит и алертит — сайзинг и
входы не трогает."""
import pytest
from src.position_guard import evaluate_exit, regime_changed


SIG_SHORT = {"direction": "SHORT", "entry": 60006.0, "sl": 64518.0, "tp": 53237.0}
SIG_LONG = {"direction": "LONG", "entry": 59542.0, "sl": 58062.0, "tp": 61762.0}


def test_short_sl_breach():
    r = evaluate_exit(SIG_SHORT, price=64600.0, verdict="SHORT", regime="TRANSITION")
    assert r and r["reason"] == "sl_breach"
    assert r["pnl_r"] == pytest.approx(-1.018, abs=0.01)


def test_short_sl_breach_beats_verdict_flip():
    """Приоритет: пробой стопа важнее любого сигнального выхода."""
    r = evaluate_exit(SIG_SHORT, price=64600.0, verdict="WAIT", regime="BULL")
    assert r["reason"] == "sl_breach"


def test_short_tp_hit():
    r = evaluate_exit(SIG_SHORT, price=53100.0, verdict="SHORT", regime="BEAR")
    assert r and r["reason"] == "tp_hit" and r["pnl_r"] > 1.4


def test_short_verdict_flip():
    """Кейс 03.07: вердикт ушёл SHORT->WAIT при живом стопе — выходим."""
    r = evaluate_exit(SIG_SHORT, price=61800.0, verdict="WAIT", regime="BULL")
    assert r and r["reason"] == "verdict_flip"
    assert r["pnl_r"] == pytest.approx(-0.398, abs=0.01)


def test_short_regime_flip_without_verdict_change():
    """Режим перевернулся в BULL, вердикт (ещё) SHORT — выходим по режиму."""
    r = evaluate_exit(SIG_SHORT, price=61000.0, verdict="SHORT", regime="BULL")
    assert r and r["reason"] == "regime_flip"


def test_long_symmetric():
    assert evaluate_exit(SIG_LONG, price=57900.0, verdict="LONG", regime="BULL")["reason"] == "sl_breach"
    assert evaluate_exit(SIG_LONG, price=61900.0, verdict="LONG", regime="BULL")["reason"] == "tp_hit"
    assert evaluate_exit(SIG_LONG, price=60000.0, verdict="LONG", regime="BEAR")["reason"] == "regime_flip"


def test_healthy_position_no_exit():
    assert evaluate_exit(SIG_SHORT, price=59000.0, verdict="SHORT", regime="BEAR") is None
    assert evaluate_exit(SIG_LONG, price=60500.0, verdict="LONG", regime="BULL") is None


def test_missing_price_fail_safe():
    """Нет цены — нет решения (не выходим вслепую), но это не exception."""
    assert evaluate_exit(SIG_SHORT, price=None, verdict="SHORT", regime="BEAR") is None


def test_regime_change_detection_and_dedup():
    st = {}
    assert regime_changed(st, "BULL") is True      # первый раз — событие
    assert st["last_regime"] == "BULL"
    assert regime_changed(st, "BULL") is False     # дедуп
    assert regime_changed(st, "BEAR") is True      # разворот — событие


# ── Реальность vs модель (04.07): оператор не смог отличить трекинг сигналов
# от реального портфеля — «закрыть позицию» кричало про позиции, которых нет.
# Формулировка алерта обязана сверяться с портфелем. ──
from src.position_guard import format_exit_alert


EX = {"reason": "sl_breach", "direction": "SHORT", "entry": 1572.0,
      "exit_price": 1760.0, "pnl_r": -1.2, "sl": 1728.0, "tp": 1339.0}


def test_alert_real_position_says_close():
    msg = format_exit_alert("ETH", EX, real_side="SHORT")
    assert "закрой" in msg.lower() and "портфеле" in msg.lower()
    assert "модельн" not in msg.lower()


def test_alert_paper_position_says_model_no_action():
    msg = format_exit_alert("ETH", EX, real_side="FLAT")
    assert "модельн" in msg.lower()
    assert "действий не требуется" in msg.lower()


def test_alert_unknown_portfolio_is_neutral():
    msg = format_exit_alert("ETH", EX, real_side=None)
    assert "проверь портфель" in msg.lower()


def test_main_block_is_last_statement():
    """Регрессия 05.07: def, дописанный ПОСЛЕ `if __name__` блока, дал
    NameError при python -m (run() исполняется раньше нижних определений) —
    гвард молча крашился с c45a586. Блок __main__ обязан быть последним."""
    import pathlib
    src = pathlib.Path("src/position_guard.py").read_text()
    tail = src[src.index('if __name__ == "__main__"'):]
    assert "def " not in tail, "определения ниже __main__-блока недопустимы"
