"""Шорт экстремально выросших монет (02.09.2026).

Первая идея за всё исследование, идущая ПРОТИВ тренда. Два основания
работают в одну сторону: возврат к среднему и фандинг — во время роста
ставка положительна, и шорт её ПОЛУЧАЕТ, тогда как весь месяц наши сигналы
были лонгами, платившими 11% годовых.

Довод против, который надо мерить: асимметрия. Убыток шорта не ограничен,
прибыль ограничена сотней процентов, а выросшие монеты выносят шорты
резко. Поэтому отдельно считается ХУДШАЯ сделка — средняя доходность её
прячет.

Контроль обязателен: шорт в случайный момент. Без него нельзя отличить
«работает шорт после роста» от «работает шорт вообще, потому что монета
падала» — эту ошибку мы уже делали дважды.
"""
import pytest

from src.pump_fade import (
    DEFAULT_PUMP_PCT, baseline, evaluate, find_pumps,
)


def _pump_then_fade(n=200):
    """Рост вдвое за неделю, затем откат."""
    out = [100.0] * 50
    out += [100.0 * (1.12 ** i) for i in range(1, 8)]      # +100% за 7 дней
    peak = out[-1]
    out += [peak * (0.97 ** i) for i in range(1, n - len(out) + 1)]
    return out


def _steady_rise(n=200):
    return [100.0 * (1.005 ** i) for i in range(n)]


def test_pump_detected():
    events = find_pumps(_pump_then_fade(), pump_pct=50, window_d=7)
    assert events
    assert events[0].run_up_pct >= 50


def test_steady_rise_is_not_a_pump():
    assert find_pumps(_steady_rise(), pump_pct=50, window_d=7) == []


def test_cooldown_prevents_duplicate_events():
    """Один затяжной рост не должен дать два десятка «событий» —
    та же передискретизация, что ловили у китов и в барьерах."""
    closes = [100.0 * (1.15 ** i) for i in range(60)]
    many = find_pumps(closes, 50, 7, cooldown_d=1)
    few = find_pumps(closes, 50, 7, cooldown_d=14)
    assert len(few) < len(many)


def test_short_profits_only_after_the_pump_exhausts():
    """Сигнал срабатывает В СЕРЕДИНЕ роста, а не на вершине.

    На горизонте 14 дней цена ещё не опускается ниже входа — рост
    продолжается против шорта. Это не дефект замера, а главный риск самой
    идеи: порог пройден, но вершина впереди. Прибыль появляется только на
    длинном горизонте, когда откат перевешивает продолжение."""
    closes = _pump_then_fade(n=260)
    events = find_pumps(closes, 50, 7)
    assert events
    short_h = evaluate(closes, events, horizon_d=14)
    long_h = evaluate(closes, events, horizon_d=60)
    assert short_h.avg_pct < long_h.avg_pct
    assert long_h.avg_pct > 0


def test_short_loses_when_pump_continues():
    closes = [100.0] * 50 + [100.0 * (1.12 ** i) for i in range(1, 60)]
    events = find_pumps(closes, 50, 7)
    r = evaluate(closes, events, horizon_d=14)
    if r.n:
        assert r.avg_pct < 0


def test_worst_trade_is_reported():
    """Средняя прячет риск сквиза — худшая сделка обязана быть видна."""
    closes = _pump_then_fade()
    r = evaluate(closes, find_pumps(closes, 50, 7), horizon_d=14)
    assert r.worst_pct is not None
    assert r.worst_pct <= r.avg_pct


def test_funding_helps_the_short():
    """Знак фандинга переворачивается в нашу пользу — это не мелочь."""
    closes = _pump_then_fade()
    r = evaluate(closes, find_pumps(closes, 50, 7), horizon_d=30)
    assert r.avg_with_funding_pct > r.avg_pct


def test_baseline_is_computed_on_random_entries():
    b = baseline(_steady_rise(), horizon_d=14)
    assert b.n > 5
    assert b.avg_pct < 0          # шорт растущей монеты убыточен


def test_no_events_is_safe():
    r = evaluate([100.0] * 50, [], horizon_d=7)
    assert r.n == 0 and r.avg_pct is None


def test_default_threshold_is_explicit():
    assert DEFAULT_PUMP_PCT == 50.0


# ------------------------------------------- стопы: моделирование показало,
# что преимущество их скорее всего переживает, но это надо проверить

def test_stop_caps_the_worst_trade():
    closes = [100.0] * 50 + [100.0 * (1.12 ** i) for i in range(1, 60)]
    events = find_pumps(closes, 50, 7)
    no_stop = evaluate(closes, events, horizon_d=30)
    with_stop = evaluate(closes, events, horizon_d=30, stop_pct=15)
    assert with_stop.worst_pct >= -15.0
    assert with_stop.worst_pct > no_stop.worst_pct


def test_stop_uses_highs_not_closes():
    """Цена может выбить стоп внутри дня и вернуться. Считать это
    выживанием значило бы завышать результат."""
    closes = [100.0] * 50 + [100.0 * 1.5] + [100.0] * 60
    highs = [c * 1.0 for c in closes]
    highs[51] = 300.0                       # выброс внутри дня
    events = find_pumps(closes, 40, 7)
    if not events:
        pytest.skip("событие не сработало на фикстуре")
    by_close = evaluate(closes, events, 30, stop_pct=20)
    by_high = evaluate(closes, events, 30, stop_pct=20, highs=highs)
    assert by_high.stopped_share >= by_close.stopped_share


def test_stopped_share_reported():
    closes = [100.0] * 50 + [100.0 * (1.12 ** i) for i in range(1, 60)]
    r = evaluate(closes, find_pumps(closes, 50, 7), 30, stop_pct=10)
    assert r.stopped_share is not None
    assert 0.0 <= r.stopped_share <= 1.0


def test_no_stop_keeps_previous_behaviour():
    closes = _pump_then_fade(n=260)
    events = find_pumps(closes, 50, 7)
    a = evaluate(closes, events, 30)
    b = evaluate(closes, events, 30, stop_pct=None)
    assert a.avg_pct == b.avg_pct
