"""H1b — короткий режим: вход на смене, выход на обратной смене (15.10.2026).

06.10 15:13 режим BEAR → BULL, тактика открыла 8 лонгов; 08.10 06:08 режим
вернулся в BEAR, гвард закрыл все восемь по regime_flip: 7 в минусе, −4.16R.
H1 такой дребезг не видит — он ищет повторный вход после выхода, а здесь
повтора нет. Новая гипотеза, критерии зафиксированы ДО данных, на которых
она проверяется: в счёт идут только выходы после H1B_REGISTERED.
"""
from datetime import datetime, timedelta, timezone

from src.h1_metrics import (
    H1B_MAX_HOLD_H, H1B_MIN_EVENTS, H1B_REGISTERED, compute_h1, compute_h1b,
    verdict_h1b,
)

T0 = datetime.fromisoformat(H1B_REGISTERED) + timedelta(days=1)


def _pair(coin, hold_h, r, event):
    entry_ts = event - timedelta(hours=hold_h)
    return [
        {"ts": entry_ts.isoformat(), "coin": coin, "direction": "LONG",
         "entry": 100.0 + hold_h, "emitted": True},
        {"ts": event.isoformat(), "coin": coin, "direction": "EXIT",
         "exit_reason": "regime_flip", "entry": 100.0 + hold_h,
         "closed_direction": "LONG", "pnl_r": r, "policy_version": 3},
    ]


def test_short_and_long_holds_split_and_events_counted_once():
    ev = T0
    rows = (_pair("BTC", 39, -0.85, ev) + _pair("ETH", 39, -0.95, ev)
            + _pair("HYPE", 200, +0.9, ev))
    res = compute_h1b(rows)
    assert res.n_events == 1                     # одна смена — одно наблюдение
    assert res.short_event_sums == [-1.8]
    assert res.n_short_exits == 2 and res.n_long_exits == 1


def test_exits_before_registration_do_not_count():
    old = datetime.fromisoformat(H1B_REGISTERED) - timedelta(days=7)
    rows = _pair("BTC", 39, -0.85, old)
    assert compute_h1b(rows).n_events == 0
    assert compute_h1b(rows, since=None).n_events == 1   # описательно — можно


def test_verdict_waits_for_sample():
    rows = _pair("BTC", 10, -1.0, T0)
    assert "НЕДОСТАТОЧНО" in verdict_h1b(compute_h1b(rows))


def test_verdict_confirms_when_median_event_negative():
    rows = []
    for i in range(H1B_MIN_EVENTS):
        rows += _pair("BTC", 30, -0.5, T0 + timedelta(days=3 * i))
    assert "ПОДТВЕРЖДЕНА" in verdict_h1b(compute_h1b(rows))
    assert "НЕ ПОДТВЕРЖДЕНА" not in verdict_h1b(compute_h1b(rows))


def test_verdict_closes_when_short_holds_pay():
    rows = []
    for i in range(H1B_MIN_EVENTS):
        rows += _pair("BTC", 30, +0.3, T0 + timedelta(days=3 * i))
    assert "НЕ ПОДТВЕРЖДЕНА" in verdict_h1b(compute_h1b(rows))


def test_hold_boundary_is_strict():
    rows = _pair("BTC", H1B_MAX_HOLD_H, -1.0, T0)
    assert compute_h1b(rows).n_short_exits == 0


def test_h1_can_be_measured_on_current_policy():
    rows = _pair("BTC", 39, -0.85, T0)
    assert compute_h1(rows, T0, 5, policy_version=3).n_exits == 1
    assert compute_h1(rows, T0, 5).n_exits == 0          # по умолчанию v2
