"""Дайджест китов считает наблюдения, а не копии (29.09.2026).

Сводка 29.09: «Всего сигналов: 229», «BTC от 0x93ab7d… ×173». Монитор
пишет по сигналу на каждый крупный филл, и 173 копии — одна позиция одного
кита. §5: наблюдение — кит + монета + сторона; §6: счётчик считает то, что
показано. Входы китов печатаются от большего объёма к меньшему.
"""
from datetime import datetime, timezone

from src.whale_correlation import Signal, SIG_CLUSTER, SIG_NEW_OPEN, SIG_OVERLAP
from src.whale_report import render_digest

NOW = datetime(2026, 9, 29, 0, 9, tzinfo=timezone.utc)
W1 = "0x93ab7dbd00000000000000000000000000000001"
W2 = "0xec4a6f5900000000000000000000000000000002"


def _overlap(whale, coin="BTC", side="long", n=1, **extra):
    return [Signal(rule=SIG_OVERLAP, severity=1, coin=coin, message="m",
                   details={"coin": coin, "whale": whale, "whale_side": side,
                            "user_side": side, "winrate_used": 1.0,
                            "notional_usd": 100_000, **extra})
            for _ in range(n)]


def _new_open(whale, coin, notional, side="long"):
    return Signal(rule=SIG_NEW_OPEN, severity=1, coin=coin, message="m",
                  details={"coin": coin, "whale": whale, "direction": side,
                           "notional_usd": notional, "winrate_used": 0.7})


def test_headline_counts_observations_not_copies():
    """173 копии одного кита + его же вход по той же монете и стороне — одно
    наблюдение; второй кит — второе."""
    sigs = (_overlap(W1, n=173) + [_new_open(W1, "BTC", 33e6)]
            + _overlap(W2, n=38))
    msg = render_digest(sigs, NOW)
    assert "Всего сигналов: 2" in msg


def test_other_side_or_coin_is_separate_observation():
    sigs = [_new_open(W1, "BTC", 1e6), _new_open(W1, "BTC", 1e6, side="short"),
            _new_open(W1, "ETH", 1e6)]
    msg = render_digest(sigs, NOW)
    assert "Всего сигналов: 3" in msg


def test_cluster_counts_once():
    c = Signal(rule=SIG_CLUSTER, severity=1, coin="ZEC", message="cluster",
               details={"coin": "ZEC", "direction": "short", "whale_count": 3,
                        "whales": ["a", "b", "c"]})
    msg = render_digest([c, c], NOW)
    assert "Всего сигналов: 1" in msg


def test_overlap_line_has_no_copy_count():
    msg = render_digest(_overlap(W1, n=173), NOW)
    assert "×173" not in msg
    assert "×" not in msg


def test_new_open_counts_distinct_whales():
    """×N во входах — число разных китов, а не прогонов, в которых кит
    попался повторно."""
    sigs = [_new_open(W1, "BTC", 10e6), _new_open(W1, "BTC", 5e6),
            _new_open(W2, "BTC", 1e6)]
    msg = render_digest(sigs, NOW)
    assert "$16.0M ×2" in msg


def test_new_open_sorted_by_volume_descending():
    """Ровно список 29.09 — объёмы печатаются от большего к меньшему."""
    sigs = []
    spec = [("ZEC", "short", 11.8e6, 5), ("BTC", "long", 107.4e6, 5),
            ("NEAR", "long", 1.3e6, 2), ("PUMP", "short", 1.6e6, 2),
            ("HYPE", "long", 3.4e6, 2), ("DOGE", "short", 250e3, 1)]
    for coin, side, total, n in spec:
        for i in range(n):
            sigs.append(_new_open(f"0x{coin}{i}", coin, total / n, side))
    msg = render_digest(sigs, NOW)
    order = [c for c, *_ in sorted(spec, key=lambda x: -x[2])]
    pos = [msg.find(f"<code>{c}</code>") for c in order]
    assert all(p != -1 for p in pos)
    assert pos == sorted(pos), order


def test_new_open_does_not_mix_sides():
    """28.09: ZEC-лонг на $3.6M попадал в строку «ZEC SHORT» — объём
    противоположной стороны подписывался чужим направлением."""
    sigs = [_new_open("0xa", "ZEC", 3.6e6, "long"),
            _new_open("0xb", "ZEC", 4.8e6, "short"),
            _new_open("0xc", "ZEC", 1.5e6, "short")]
    msg = render_digest(sigs, NOW)
    assert "<code>ZEC</code> SHORT • $6.3M ×2" in msg
    assert "<code>ZEC</code> LONG • $3.6M" in msg


def test_single_whale_line_shows_its_record():
    """30.09: «BTC SHORT • $37.7M» — один кит с ОДНИМ закрытием за всю
    историю, а строка выглядела как крупный сигнал. Когда за строкой один
    кит, его WR и число закрытий печатаются, как в мгновенном алерте."""
    s = _new_open("0xedcdca", "BTC", 37.7e6, "short")
    s.details["winrate_used"] = 1.0
    s.details["closures_used"] = 1
    msg = render_digest([s], NOW)
    assert "<code>BTC</code> SHORT • $37.7M (WR 100% · закрытий: 1)" in msg


def test_multi_whale_line_has_no_single_record():
    sigs = [_new_open("0xa", "ZEC", 1e6), _new_open("0xb", "ZEC", 1e6)]
    msg = render_digest(sigs, NOW)
    assert "<code>ZEC</code> LONG • $2.0M ×2" in msg
    assert "WR" not in msg
