"""Состав кластера китов в алерте (30.09.2026).

«⚡ CLUSTER BTC LONG — 5 китов» 30.09: у троих из пяти WR по BTC 41–54% на
2–4 закрытиях, сильная история — у одного. Кластер требует лишь статуса
«оценён», без порога WR, и «N китов» преувеличивает согласие: замер по
истории (17 кластеров, сентябрь) — в 11 из них больше половины объёма даёт
один кит, в 7 нет ни одного кита с историей.

Предсказательной силы у признаков на 17 кластерах не найдено (шум), поэтому
это ПОКАЗ фактов, а не оценка кластера. Отбор не меняется.
"""
from datetime import datetime, timedelta, timezone

from src.whale_correlation import (
    CorrelationConfig, Signal, SIG_CLUSTER, detect_cluster,
)
from src.whale_report import render_instant_alerts
from src.whale_scoring import CoinStats, OK, WhaleScore
from src.whale_tracker import WhaleFill

NOW = datetime(2026, 9, 30, 17, 34, tzinfo=timezone.utc)


def _open(whale, notional, coin="BTC", direction="Open Long", tid=0):
    return WhaleFill(
        whale=whale, coin=coin, side="B", direction=direction, size=1.0,
        price=1.0, notional_usd=notional, tid=tid,
        time_ms=int((NOW - timedelta(minutes=30)).timestamp() * 1000),
        closed_pnl=0.0, crossed=True, oid=tid,
    )


def _score(whale, wr, closures, coin="BTC"):
    return WhaleScore(
        whale=whale, status=OK, closed_trades=100, win_rate=0.5,
        total_pnl=1, avg_pnl=1, best_trade=1, worst_trade=1, window_days=90,
        by_coin={coin: CoinStats(coin=coin, closed_trades=50, win_rate=wr,
                                 total_pnl=1, avg_pnl=1, closures=closures)},
        closures=closures,
    )


def _btc_30_09():
    """Состав 30.09: один проверенный кит (5 закрытий, 89%) из пяти."""
    spec = [("0xec4a6f", 25.5e6, 0.89, 5), ("0xdd7a37", 11.3e6, 1.00, 4),
            ("0x051c2e", 0.6e6, 0.45, 4), ("0x1367df", 0.4e6, 0.41, 2),
            ("0xe09726", 0.3e6, 0.54, 2)]
    fills = [_open(w, n, tid=i) for i, (w, n, *_ ) in enumerate(spec)]
    scores = {w: _score(w, wr, cl) for w, _, wr, cl in spec}
    return fills, scores


def test_cluster_details_carry_volume_share_and_proven():
    fills, scores = _btc_30_09()
    sigs = detect_cluster(fills, scores, {"BTC"}, CorrelationConfig())
    assert len(sigs) == 1
    d = sigs[0].details
    assert d["whale_count"] == 5                      # отбор тот же
    assert round(d["notional_usd"] / 1e6, 1) == 38.1
    assert round(d["top_share"], 2) == 0.67
    assert d["proven_whales"] == 1


def test_proven_needs_both_closures_and_winrate():
    """0xdd7a37: WR 100%, но 4 закрытия — не проверенный. Кит с 20
    закрытиями и WR 55% — тоже нет."""
    fills = [_open("0xa", 1e6, tid=1), _open("0xb", 1e6, tid=2),
             _open("0xc", 1e6, tid=3)]
    scores = {"0xa": _score("0xa", 1.00, 4), "0xb": _score("0xb", 0.55, 20),
              "0xc": _score("0xc", 0.60, 5)}
    d = detect_cluster(fills, scores, {"BTC"}, CorrelationConfig())[0].details
    assert d["proven_whales"] == 1


def test_alert_line_shows_volume_proven_and_dominant_whale():
    fills, scores = _btc_30_09()
    sig = detect_cluster(fills, scores, {"BTC"}, CorrelationConfig())[0]
    msg = render_instant_alerts([sig], NOW)
    assert "CLUSTER BTC</b> LONG — 5 китов • $38.1M" in msg
    assert "с историей 1 из 5" in msg
    assert "один кит — 67% объёма" in msg


def test_zero_proven_is_said_out_loud():
    """Ноль — не отсутствие данных: «с историей 0 из 3» печатается."""
    fills = [_open(w, 1e6, tid=i) for i, w in enumerate(("0xa", "0xb", "0xc"))]
    scores = {w: _score(w, 0.45, 3) for w in ("0xa", "0xb", "0xc")}
    sig = detect_cluster(fills, scores, {"BTC"}, CorrelationConfig())[0]
    msg = render_instant_alerts([sig], NOW)
    assert "с историей 0 из 3" in msg
    assert "один кит" not in msg                       # объём поровну


def test_old_cluster_signal_renders_as_before():
    s = Signal(rule=SIG_CLUSTER, severity=2, coin="ETH", message="m",
               details={"coin": "ETH", "direction": "long", "whale_count": 3,
                        "whales": ["a", "b", "c"], "focus": True})
    msg = render_instant_alerts([s], NOW)
    assert "CLUSTER ETH</b> LONG — 3 китов" in msg
    assert "с историей" not in msg
