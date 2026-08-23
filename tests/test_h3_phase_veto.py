"""H3 — фаза перестаёт в одиночку вето́ровать сильный тренд (23.08.2026).

Что было. `direction_permission()`: при `regime != BULL` и медвежьей фазе
лонг запрещён. Логика правильная для BEAR — там вето ставит режим. Но при
`TRANSITION` режим направления не имеет, и решение целиком отдавалось фазе.

Фаза считается по MVRV и по построению медленная. 18→21.08 она простояла
на `EARLY_BEAR`, пока рынок рос на +15…+29% по всем девяти монетам. Сырой
слой выдал 26 LONG, 23 из них погашены, 19 — этим самым правилом. Неделя
без единой идеи в канале.

Что стало. При неопределённом режиме фаза больше не отменяет ПОЛНОЦЕННЫЙ
тренд (|trend_score| == 2 — EMA50/EMA200 выстроены). Слабый сигнал
(|trend_score| == 1 — отскок в нисходящем тренде, коррекция в восходящем)
она вето́рует по-прежнему: там тренда как такового нет, и медленный
цикловой слой добавляет настоящую информацию.

Что НЕ изменилось. `regime == BEAR` запрещает лонг, `regime == BULL`
запрещает шорт — вето режима остаётся абсолютным. Правка касается только
случая, когда режим сам не знает направления.
"""
import pytest

from src.eth_focus import direction_permission

STRONG, WEAK = 2, 1


# ------------------------------------------- вето режима остаётся абсолютным

@pytest.mark.parametrize("score", [STRONG, WEAK])
def test_bear_regime_still_blocks_long(score):
    allowed, note = direction_permission("LONG", "BEAR", "EARLY_BULL", score)
    assert allowed is False
    assert "BEAR" in note


@pytest.mark.parametrize("score", [-STRONG, -WEAK])
def test_bull_regime_still_blocks_short(score):
    allowed, note = direction_permission("SHORT", "BULL", "MID_BEAR", score)
    assert allowed is False
    assert "BULL" in note


def test_bull_regime_allows_long_regardless_of_phase():
    assert direction_permission("LONG", "BULL", "EARLY_BEAR", STRONG)[0] is True


# --------------------------------- TRANSITION: сильный тренд проходит фазу

def test_transition_strong_uptrend_passes_bear_phase():
    """Ровно случай 18→21.08: тренд вверх, фаза EARLY_BEAR, режим TRANSITION."""
    allowed, note = direction_permission("LONG", "TRANSITION", "EARLY_BEAR",
                                         STRONG)
    assert allowed is True
    assert note is None


def test_transition_strong_downtrend_passes_bull_phase():
    allowed, _ = direction_permission("SHORT", "TRANSITION", "MID_BULL",
                                      -STRONG)
    assert allowed is True


def test_none_regime_behaves_like_transition():
    assert direction_permission("LONG", None, "EARLY_BEAR", STRONG)[0] is True


# ------------------------------- TRANSITION: слабый сигнал фаза по-прежнему бьёт

def test_transition_weak_long_still_blocked_by_bear_phase():
    """Отскок в нисходящем тренде — не тренд; фаза здесь информативна."""
    allowed, note = direction_permission("LONG", "TRANSITION", "EARLY_BEAR",
                                         WEAK)
    assert allowed is False
    assert "EARLY_BEAR" in note


def test_transition_weak_short_still_blocked_by_bull_phase():
    allowed, note = direction_permission("SHORT", "TRANSITION", "MARKUP",
                                         -WEAK)
    assert allowed is False


def test_neutral_phase_never_blocks():
    for score in (STRONG, WEAK):
        assert direction_permission("LONG", "TRANSITION", "MID_BULL",
                                    score)[0] is True


# ----------------------------------------------------- обратная совместимость

def test_trend_score_defaults_to_old_behaviour():
    """Без trend_score правило работает как до 23.08 — фаза ветует."""
    allowed, note = direction_permission("LONG", "TRANSITION", "EARLY_BEAR")
    assert allowed is False
    assert "EARLY_BEAR" in note


def test_wait_verdict_is_never_blocked():
    assert direction_permission("WAIT", "TRANSITION", "EARLY_BEAR",
                                STRONG)[0] is True
