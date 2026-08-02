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


# ── 17.07: модельный выход без позиции — журнал да, пуш нет ─────────────────

def test_exit_alert_needed_only_when_not_flat():
    """Пуш «в портфеле позиции нет, действий не требуется» — шум: нет
    действия, не нужно уведомление. Журнальная запись остаётся всегда
    (exit_reason кормит ворота zone_strength 20.07). None (портфель
    проверить не удалось) по-прежнему алертит — перестраховка."""
    from src.position_guard import exit_alert_needed
    assert exit_alert_needed("LONG") is True
    assert exit_alert_needed("SHORT") is True
    assert exit_alert_needed(None) is True     # проверка упала — алертим
    assert exit_alert_needed("FLAT") is False  # подтверждённо вне позиции


# ===================== Гистерезис verdict_flip (02.08) =====================
# Пре-регистрация: docs/PLAN_UNFREEZE_20_07.md п.1. K=3 откалиброван на
# журнале с 01.07 (шумовые серии WAIT: 1-2 тика, настоящие: 6+).

from src.position_guard import (  # noqa: E402
    FLIP_CONFIRM_RUNS, POLICY_VERSION, is_reversal, next_flip_streak,
)


def _long_sig(entry=100.0, sl=90.0, tp=130.0):
    return {"direction": "LONG", "entry": entry, "sl": sl, "tp": tp}


def _short_sig(entry=100.0, sl=110.0, tp=70.0):
    return {"direction": "SHORT", "entry": entry, "sl": sl, "tp": tp}


def test_k_is_three():
    """K зафиксирован в коде: разрыв в данных между 2 и 6 тиками."""
    assert FLIP_CONFIRM_RUNS == 3


def test_single_wait_dip_does_not_exit():
    """Нырок на один прогон — шум, позиция держится."""
    ex = evaluate_exit(_long_sig(), price=105.0, verdict="WAIT",
                       regime=None, flip_streak=1)
    assert ex is None


def test_two_wait_dips_still_hold():
    ex = evaluate_exit(_long_sig(), price=105.0, verdict="WAIT",
                       regime=None, flip_streak=2)
    assert ex is None


def test_k_consecutive_wait_triggers_exit():
    """На K-м подряд прогоне выход состоится."""
    ex = evaluate_exit(_long_sig(), price=105.0, verdict="WAIT",
                       regime=None, flip_streak=FLIP_CONFIRM_RUNS)
    assert ex is not None
    assert ex["reason"] == "verdict_flip"


def test_opposite_verdict_exits_immediately_long():
    """LONG + вердикт SHORT = разворот, гистерезис не применяется."""
    ex = evaluate_exit(_long_sig(), price=105.0, verdict="SHORT",
                       regime=None, flip_streak=1)
    assert ex is not None
    assert ex["reason"] == "verdict_flip"


def test_opposite_verdict_exits_immediately_short():
    ex = evaluate_exit(_short_sig(), price=95.0, verdict="LONG",
                       regime=None, flip_streak=1)
    assert ex is not None
    assert ex["reason"] == "verdict_flip"


def test_streak_resets_when_verdict_returns():
    assert next_flip_streak(2, "LONG", "LONG") == 0
    assert next_flip_streak(2, None, "LONG") == 0


def test_streak_increments_while_off_side():
    assert next_flip_streak(0, "WAIT", "LONG") == 1
    assert next_flip_streak(1, "WAIT", "LONG") == 2
    assert next_flip_streak(2, "WAIT", "LONG") == 3


def test_is_reversal_only_for_opposite_side():
    assert is_reversal("SHORT", "LONG") is True
    assert is_reversal("LONG", "SHORT") is True
    assert is_reversal("WAIT", "LONG") is False
    assert is_reversal(None, "LONG") is False


def test_sl_breach_ignores_hysteresis():
    """Стоп — пре-коммит входа, гистерезис на него не распространяется."""
    ex = evaluate_exit(_long_sig(), price=89.0, verdict="WAIT",
                       regime=None, flip_streak=1)
    assert ex["reason"] == "sl_breach"


def test_regime_flip_fires_while_flip_pending():
    """Режимный разворот — независимая причина, висящий flip её не глушит."""
    ex = evaluate_exit(_long_sig(), price=105.0, verdict="WAIT",
                       regime="BEAR", flip_streak=1)
    assert ex is not None
    assert ex["reason"] == "regime_flip"


def test_default_flip_streak_preserves_old_behaviour():
    """Вызов без flip_streak (старый код/тесты) выходит сразу, как раньше."""
    ex = evaluate_exit(_long_sig(), price=105.0, verdict="WAIT", regime=None)
    assert ex is not None
    assert ex["reason"] == "verdict_flip"


def test_policy_version_is_two():
    assert POLICY_VERSION == 2
