"""Один вход на сторону — самый выгодный относительно BTC (27.10.2026).

Решение оператора 27.10: «все эти позиции в одном направлении — давать
сигнал только по самой выгодной относительно BTC». Отменяет его же
решение 18.09 («топ-3»): три лонга по альтам с корреляцией около единицы —
одна ставка втрое крупнее, а счёт тянет одну сделку.

Выбор по силе против BTC — лучшее, что есть, но НЕ доказанное
преимущество: H4 дала +0.128 при пороге 0.20 (01.09). Поэтому остальные
входы названы строкой, не спрятаны.

Для лонга выгоднее сильнейшая монета, для шорта — слабейшая.
"""
from src.digest_compact import keep_best_per_side


def _v(coin, verdict="LONG", rs=None):
    return (coin, 1.0, verdict, "тренд вверх.", verdict, "тренд вверх.", rs)


def test_one_long_strongest_vs_btc():
    """27.10: ZEC −16, ASTER +4, NEAR −13 → показан ASTER."""
    v = [_v("ZEC", rs=-16.0), _v("ASTER", rs=4.0), _v("NEAR", rs=-13.0)]
    kept, line = keep_best_per_side(v)
    assert [x[0] for x in kept if x[2] == "LONG"] == ["ASTER"]
    assert "ZEC" in line and "NEAR" in line


def test_short_takes_weakest_vs_btc():
    v = [_v("TAO", "SHORT", rs=-20.0), _v("ETH", "SHORT", rs=5.0)]
    kept, _ = keep_best_per_side(v)
    assert [x[0] for x in kept if x[2] == "SHORT"] == ["TAO"]


def test_long_and_short_both_kept():
    """Разные стороны — хедж, а не одна ставка: по одному на каждую."""
    v = [_v("ZEC", rs=1.0), _v("NEAR", rs=5.0), _v("TAO", "SHORT", rs=-3.0)]
    kept, _ = keep_best_per_side(v)
    assert sorted(x[0] for x in kept if x[2] in ("LONG", "SHORT")) == \
        ["NEAR", "TAO"]


def test_non_executable_never_wins():
    v = [_v("ZEC", rs=50.0), _v("NEAR", rs=-10.0)]
    kept, _ = keep_best_per_side(v, executable={"ZEC": False, "NEAR": True})
    assert [x[0] for x in kept if x[2] == "LONG"] == ["NEAR"]


def test_unknown_rs_goes_last():
    v = [_v("ZEC", rs=None), _v("NEAR", rs=-30.0)]
    kept, _ = keep_best_per_side(v)
    assert [x[0] for x in kept if x[2] == "LONG"] == ["NEAR"]


def test_single_entry_untouched():
    v = [_v("HYPE", rs=2.0), _v("TAO", "WAIT")]
    kept, line = keep_best_per_side(v)
    assert kept == v and line == ""


def test_waits_and_nodata_preserved():
    v = [_v("HYPE", rs=1.0), _v("TAO", rs=2.0),
         _v("ZEC", "WAIT"), _v("ONDO", "NODATA")]
    kept, _ = keep_best_per_side(v)
    assert {"ZEC", "ONDO"} <= {x[0] for x in kept}


def test_line_names_basis_honestly():
    v = [_v("ZEC", rs=-16.0), _v("ASTER", rs=4.0)]
    _, line = keep_best_per_side(v)
    assert "относительно BTC" in line
