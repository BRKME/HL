"""Теневая проверка шорта новых листингов вне выборки (27.10.2026).

Бэктест прошёл правило, но интервалы не отделили эффект от случайного
шорта. Решение оператора: каждый новый листинг HL записывается модельным
шортом по правилу (вход по закрытию первого полного дня, стоп −15% по
максимумам и гэпам, выход через 30 дней). В канал не пишет.
"""
from src.listing_shadow import (
    HORIZON_D, STOP_PCT, step,
)

DAY = 86_400_000
T0 = 1_800_000_000_000


def _c(i, o, h, c):
    return {"t": T0 + i * DAY, "o": o, "h": h, "c": c}


def _flat(n, px=10.0):
    return [_c(i, px, px, px) for i in range(n)]


def test_first_run_only_remembers_coins():
    state = {}
    state, events = step(state, meta={"BTC": {}, "ETH": {}},
                         candles_of=lambda c: _flat(5))
    assert set(state["seen"]) == {"BTC", "ETH"}
    assert state["trades"] == [] and events == []


def test_new_listing_opens_trade_at_first_full_day_close():
    state = {"seen": ["BTC"], "trades": [], "pending": []}
    cs = [_c(0, 8, 9, 9), _c(1, 9, 10, 10)]
    state, events = step(state, meta={"BTC": {}, "NEW": {}},
                         candles_of=lambda c: cs)
    t = state["trades"][0]
    assert t["coin"] == "NEW" and t["entry"] == 10.0 and t["status"] == "open"
    assert events == ["открыт шорт NEW по 10"]


def test_listing_without_full_day_waits():
    state = {"seen": ["BTC"], "trades": [], "pending": []}
    state, _ = step(state, meta={"BTC": {}, "NEW": {}},
                    candles_of=lambda c: [_c(0, 8, 9, 9)])
    assert state["trades"] == [] and state["pending"] == ["NEW"]
    cs = [_c(0, 8, 9, 9), _c(1, 9, 10, 10)]
    state, _ = step(state, meta={"BTC": {}, "NEW": {}},
                    candles_of=lambda c: cs)
    assert state["trades"][0]["entry"] == 10.0 and state["pending"] == []


def _open_state():
    return {"seen": ["BTC", "NEW"], "pending": [],
            "trades": [{"coin": "NEW", "entry_t": T0 + DAY, "entry": 10.0,
                        "status": "open"}]}


def test_stop_by_intraday_high():
    cs = [_c(0, 8, 9, 9), _c(1, 9, 10, 10), _c(2, 10, 11.6, 10)]
    state, _ = step(_open_state(), meta={"BTC": {}, "NEW": {}},
                    candles_of=lambda c: cs)
    t = state["trades"][0]
    assert t["status"] == "closed" and t["ret"] == -STOP_PCT and t["stopped"]


def test_gap_through_stop_is_counted_in_full():
    cs = [_c(0, 8, 9, 9), _c(1, 9, 10, 10), _c(2, 14, 15, 14)]
    state, _ = step(_open_state(), meta={"BTC": {}, "NEW": {}},
                    candles_of=lambda c: cs)
    assert state["trades"][0]["ret"] == -40.0


def test_closes_after_horizon():
    cs = [_c(0, 8, 9, 9), _c(1, 9, 10, 10)] + [
        _c(i, 9, 9, 9) for i in range(2, 2 + HORIZON_D)]
    state, _ = step(_open_state(), meta={"BTC": {}, "NEW": {}},
                    candles_of=lambda c: cs)
    t = state["trades"][0]
    assert t["status"] == "closed" and round(t["ret"], 6) == 10.0
    assert not t["stopped"]


def test_still_open_inside_horizon():
    cs = [_c(0, 8, 9, 9), _c(1, 9, 10, 10), _c(2, 10, 10.5, 10)]
    state, _ = step(_open_state(), meta={"BTC": {}, "NEW": {}},
                    candles_of=lambda c: cs)
    assert state["trades"][0]["status"] == "open"


def test_delisted_while_open_closes_at_last_close():
    cs = [_c(0, 8, 9, 9), _c(1, 9, 10, 10), _c(2, 10, 10, 5)]
    state, _ = step(_open_state(), meta={"BTC": {}, "NEW": {"delisted": True}},
                    candles_of=lambda c: cs)
    t = state["trades"][0]
    assert t["status"] == "closed" and t["ret"] == 50.0
    assert t.get("reason") == "delisted"


def test_candle_failure_keeps_trade_open():
    def boom(c):
        raise RuntimeError("сеть")
    state, _ = step(_open_state(), meta={"BTC": {}, "NEW": {}},
                    candles_of=boom)
    assert state["trades"][0]["status"] == "open"
