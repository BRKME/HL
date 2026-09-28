"""Число закрытий позиций рядом с WR кита (28.09.2026).

WR кита считается по ЗАКРЫВАЮЩИМ ФИЛЛАМ, а биржа дробит одно закрытие на
сотни филлов. Кит 0xdd7a37… показывался как «WR 100%» по BTC на 177
«сделках», а закрытий позиции было два. Пока подсчёт не пересмотрен на
чекпойнте, рядом с WR печатается число закрытий — чтобы видно было, на
чём стоит процент. Отбор китов при этом НЕ меняется.
"""
from datetime import datetime, timedelta, timezone

from src.whale_correlation import (
    CorrelationConfig, Signal, SIG_OVERLAP, detect_overlap,
)
from src.whale_report import render_digest
from src.whale_scoring import CoinStats, OK, WhaleScore, score_from_fills
from src.whale_tracker import WhaleFill
from src.portfolio import AggregatedPerpPosition

NOW = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)
WHALE = "0xdd7a372377fc633f74ab6e20963803d52f448830"


def _close(coin, t, pnl=100.0, tid=0):
    return WhaleFill(
        whale=WHALE, coin=coin, side="A", direction="Close Long",
        size=1.0, price=1.0, notional_usd=10_000.0, tid=tid,
        time_ms=int(t.timestamp() * 1000), closed_pnl=pnl,
        crossed=True, oid=tid,
    )


def _burst(coin, start, n, tid0):
    """Одно закрытие позиции, раздробленное на n филлов в пределах минут."""
    return [_close(coin, start + timedelta(seconds=5 * i), tid=tid0 + i)
            for i in range(n)]


def test_closures_count_episodes_not_fills():
    fills = (_burst("BTC", NOW - timedelta(days=9), 7, 0)
             + _burst("BTC", NOW - timedelta(hours=2), 170, 100))
    s = score_from_fills(fills, WHALE, NOW)
    assert s.status == OK
    assert s.by_coin["BTC"].closed_trades == 177   # отбор не тронут
    assert s.by_coin["BTC"].closures == 2
    assert s.closures == 2


def test_closures_split_by_gap_and_summed_across_coins():
    t0 = NOW - timedelta(days=3)
    fills = (_burst("HYPE", t0, 6, 0)
             # разрыв больше часа — новое закрытие
             + _burst("HYPE", t0 + timedelta(hours=3), 6, 100)
             + _burst("TAO", t0, 6, 200))
    s = score_from_fills(fills, WHALE, NOW)
    assert s.by_coin["HYPE"].closures == 2
    assert s.by_coin["TAO"].closures == 1
    assert s.closures == 3


def _score(closures):
    return WhaleScore(
        whale=WHALE, status=OK, closed_trades=723, win_rate=0.64,
        total_pnl=1, avg_pnl=1, best_trade=1, worst_trade=1, window_days=90,
        by_coin={"BTC": CoinStats(coin="BTC", closed_trades=177, win_rate=1.0,
                                  total_pnl=1, avg_pnl=1, closures=closures)},
        closures=10,
    )


def _open_btc():
    return WhaleFill(
        whale=WHALE, coin="BTC", side="B", direction="Open Long",
        size=2.0, price=84000.0, notional_usd=168_000.0, tid=9,
        time_ms=int((NOW - timedelta(minutes=5)).timestamp() * 1000),
        closed_pnl=0.0, crossed=True, oid=9,
    )


def _user_btc():
    return AggregatedPerpPosition(
        coin="BTC", net_size=0.005, weighted_entry=84000.0, total_pnl=0.0,
        contributors=[("main", 0.005)], avg_leverage=3.0,
        max_liquidation_distance_pct=30.0,
    )


def test_overlap_signal_carries_closures_and_selection_unchanged():
    """Кит с двумя закрытиями по-прежнему проходит — порог отбора заморожен."""
    sigs = detect_overlap([_open_btc()], {WHALE: _score(2)},
                          [_user_btc()], CorrelationConfig())
    assert len(sigs) == 1
    assert sigs[0].details["closures_used"] == 2
    assert "WR 100% · закрытий: 2" in sigs[0].message


def test_digest_overlap_line_shows_closures():
    sig = Signal(rule=SIG_OVERLAP, severity=1, coin="BTC", message="m",
                 details={"coin": "BTC", "whale": WHALE, "winrate_used": 1.0,
                          "closures_used": 2})
    msg = render_digest([sig], NOW)
    assert "WR 100% · закрытий: 2" in msg


def test_digest_old_signal_without_closures_renders_as_before():
    """Сигналы, лежащие в буфере с прошлых прогонов, поля не имеют."""
    sig = Signal(rule=SIG_OVERLAP, severity=1, coin="BTC", message="m",
                 details={"coin": "BTC", "whale": WHALE, "winrate_used": 1.0})
    msg = render_digest([sig], NOW)
    assert "(WR 100%)" in msg
    assert "закрытий" not in msg
