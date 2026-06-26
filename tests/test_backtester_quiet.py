"""Бэктестер шлёт отчёт ТОЛЬКО при зрелых паттернах. Незрело — молчит (статистику
всё равно сохраняет). Оператор: сообщение с 'WR 100% но рано судить' = шум.
"""
from src.signal_backtester import has_actionable, BacktestGroup


def _g(coin, direction, n, wr):
    return BacktestGroup(coin=coin, rule="NEW_OPEN", direction=direction,
                         n_events=n, win_rate={24: wr, 168: wr},
                         avg_return_pct={24: 2.0, 168: 6.0},
                         max_dd_pct={24: 0.0, 168: 0.0})


def test_has_actionable_true_when_mature():
    # N≥10, WR≥0.6 И проверен разнообразием (не ровно 100%)
    results = {10_000: [_g("BTC", "SHORT", 40, 0.72)]}
    assert has_actionable(results) is True


def test_perfect_wr_not_mature():
    # WR ровно 100% = все в одну сторону, разворотом не проверено -> молчим
    results = {10_000: [_g("BTC", "SHORT", 40, 1.0)]}
    assert has_actionable(results) is False


def test_has_actionable_false_when_immature():
    results = {10_000: [_g("BTC", "SHORT", 4, 1.0)]}    # N<10
    assert has_actionable(results) is False


def test_has_actionable_false_when_weak_wr():
    results = {10_000: [_g("BTC", "SHORT", 40, 0.45)]}  # WR<0.6
    assert has_actionable(results) is False


def test_has_actionable_empty():
    assert has_actionable({}) is False
    assert has_actionable({10_000: []}) is False
