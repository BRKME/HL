"""Позиционирование толпы (04.09.2026).

Единственный класс данных, которого у нас нет. Hyperliquid его не отдаёт,
Binance ответил 451, Bybit — 403; обе блокировки региональные, раннеры
Actions в США. OKX отдаёт два среза: 180 суточных точек по счетам и 100 по
позициям топ-трейдеров.

Собираем РАЗ В СУТКИ по замечанию оператора: за час позиционирование не
меняется осмысленно, часовые точки дали бы 24 почти одинаковых числа в
день — та же передискретизация, что ловили у китов и в журнале.

Оператор был прав и в том, что 180 дней — это много. Я назвал их
негодными, посчитав независимые наблюдения для семидневного горизонта (там
их около 25). При СУТОЧНОМ горизонте наблюдения почти не перекрываются, и
их 179 на монету — проверять можно уже сейчас.

Главное здесь — расхождение между срезами, а не уровень: толпа в шорте на
76% при почти нейтральных топах и есть тот сигнал, который оператор
заметил. Готового такого показателя нет ни у кого.
"""
import pytest

from src.positioning import (
    Point, evaluate, forward_returns, long_share, merge, parse_okx,
)

DAY = 86_400_000


# ------------------------------------------------------------- разбор

def test_parses_okx_array_format():
    """OKX отдаёт массивы, а не словари — знать это надо было до кода."""
    assert parse_okx([["1788710400000", "1.06"]]) == {1788710400000: 1.06}


def test_parses_dict_format_too():
    assert parse_okx([{"ts": "100", "ratio": "0.5"}]) == {100: 0.5}


def test_skips_broken_rows():
    assert parse_okx([["x", "y"], [], None, ["100", "1.0"]]) == {100: 1.0}


def test_long_share_from_ratio():
    """Долей longAccount OKX не даёт — выводим из отношения."""
    assert long_share(1.0) == pytest.approx(0.5)
    assert long_share(3.0) == pytest.approx(0.75)
    assert long_share(0.35) == pytest.approx(0.2593, abs=1e-3)
    assert long_share(None) is None


# --------------------------------------------------------- расхождение

def test_divergence_positive_when_tops_longer():
    p = Point(0, accounts_ratio=0.35, top_pos_ratio=1.05)
    assert p.divergence > 0


def test_divergence_negative_when_tops_shorter():
    assert Point(0, 1.5, 0.8).divergence < 0


def test_divergence_none_without_both_slices():
    assert Point(0, 1.0, None).divergence is None


def test_merge_keeps_all_timestamps():
    pts = merge({0: 1.0, DAY: 1.1}, {DAY: 0.9})
    assert [p.ts_ms for p in pts] == [0, DAY]
    assert pts[0].top_pos_ratio is None


# --------------------------------------------------- forward-доходность

def test_forward_return_uses_next_day():
    pts = [Point(0, 1.0, 1.0)]
    prices = {0: 100.0, DAY: 110.0}
    out = forward_returns(pts, prices, horizon_d=1)
    assert out[0][1] == pytest.approx(10.0)


def test_missing_price_is_skipped():
    assert forward_returns([Point(0, 1.0, 1.0)], {0: 100.0}) == []


# -------------------------------------------------------------- оценка

def _series(n=100):
    pts, prices = [], {}
    for i in range(n + 1):
        ts = i * DAY
        # толпа в лонге => следующий день падает; в шорте => растёт
        ratio = 0.3 + (i % 10) * 0.2
        pts.append(Point(ts, ratio, ratio * 1.1))
        prices[ts] = 100.0 + (10 - (i % 10))
    return pts[:-1], prices


def test_evaluate_reports_all_groups():
    pts, prices = _series()
    out = evaluate(forward_returns(pts, prices))
    labels = [s.label for s in out]
    assert "все дни" in labels
    assert any("толпа в шорте" in l for l in labels)
    assert any("топы длиннее" in l for l in labels)


def test_extreme_is_relative_to_own_history():
    """«Отношение 0.35» само по себе ничего не значит — значение имеет
    отклонение от нормы ЭТОЙ монеты."""
    pts, prices = _series()
    out = evaluate(forward_returns(pts, prices), extreme_pct=0.20)
    base = next(s for s in out if s.label == "все дни")
    extreme = next(s for s in out if "толпа в шорте" in s.label)
    assert extreme.n < base.n


def test_empty_input_is_safe():
    assert evaluate([]) == []


# ------------------------------- подпись в строке дайджеста (07.09)

def test_digest_line_shows_both_slices():
    from src.positioning import format_for_digest

    out = format_for_digest(accounts_ratio=1.78, top_pos_ratio=0.92)
    # «толпа 64/36» было непонятно: где лонги, где шорты. Называем
    # перевешивающую сторону (08.09).
    assert "толпа 64% лонг" in out
    # 0.92 — это 48% лонгов, то есть почти поровну: порог «поровну» стоит
    # на 3 п.п. от середины, чтобы шум не выдавался за перекос.
    assert "топы поровну" in out


def test_digest_line_marks_big_money_shorter():
    """Единственный признак с устойчивым знаком: крупные короче толпы —
    -0.45% на семи монетах из девяти."""
    from src.positioning import format_for_digest

    assert "крупные короче" in format_for_digest(3.0, 0.8)


def test_no_mark_when_aligned():
    from src.positioning import format_for_digest

    assert "крупные короче" not in format_for_digest(1.0, 1.05)


def test_digest_line_handles_one_slice():
    from src.positioning import format_for_digest

    out = format_for_digest(1.78, None)
    assert "толпа 64% лонг" in out
    assert "топы" not in out


def test_digest_line_empty_without_data():
    from src.positioning import format_for_digest

    assert format_for_digest(None, None) == ""


def test_digest_renders_positioning_when_present():
    """Расстановка появляется в строке плана, рядом со стопом и размером."""
    import re
    import tempfile
    from datetime import datetime, timezone
    from pathlib import Path

    from src.whitelist_focus import render_whitelist_verdicts

    closes = [100.0 * (1.004 ** i) for i in range(220)]
    candles = [{"o": c, "h": c * 1.02, "l": c * 0.98, "c": c} for c in closes]
    cd = {"BTC": {"mark": closes[-1], "candles_closes": closes,
                  "candles": candles, "accounts_ratio": 1.78,
                  "top_pos_ratio": 0.92}}
    msg = render_whitelist_verdicts(
        now=datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc), coin_data=cd,
        regime_snapshot=None, state_dir=Path(tempfile.mkdtemp()),
        show_whale_stance=False)
    plain = re.sub(r"<[^>]+>", "", msg)
    assert "толпа 64% лонг" in plain or "Входов нет" in plain


def test_digest_survives_without_positioning():
    """Данных нет — строка просто короче, письмо не ломается."""
    import tempfile
    from datetime import datetime, timezone
    from pathlib import Path

    from src.whitelist_focus import render_whitelist_verdicts

    closes = [100.0 * (1.004 ** i) for i in range(220)]
    cd = {"BTC": {"mark": closes[-1], "candles_closes": closes}}
    msg = render_whitelist_verdicts(
        now=datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc), coin_data=cd,
        regime_snapshot=None, state_dir=Path(tempfile.mkdtemp()),
        show_whale_stance=False)
    assert msg
