"""Дайджест как руководство к действию (30.08.2026, запрос оператора).

Три претензии к письму от 30.08, все справедливые:

1. Пять строк «НЕ ВХОДИТЬ» — информация о том, чего делать не надо. Она
   занимала больше половины письма (политика §7.7: перечисляй отклонения,
   а не норму).
2. По входам нет стопа — открыть сделку по такому письму нельзя, надо
   ждать отдельного тактического сигнала.
3. Четыре входа без приоритета: какой брать первым, если размер один.

По третьему пункту важна оговорка. Валидированного способа ранжировать
входы у системы НЕТ. Относительная сила — кандидат из гипотезы H4, которая
зарегистрирована, но НЕ проверена. Поэтому порядок показывается вместе с
числом и с прямой пометкой, что основание не проверено: оператор видит, на
чём основан порядок, и решает сам. Отбор сигналов при этом не меняется —
измеримость H3/H4 не страдает.
"""
import pytest

from src.digest_compact import collapse_waits_when_entries, rank_entries


def _v(coin, verdict, rationale="тренд вверх.", rs=None):
    return (coin, 1.0, verdict, rationale, verdict, rationale, rs)


# ------------------------------------------- 1. свернуть «НЕ ВХОДИТЬ»

def test_waits_collapse_into_one_line_when_entries_exist():
    rows = [_v("BTC", "WAIT", "тренд вверх, но overbought (RSI 70) — ждать pullback."),
            _v("ETH", "WAIT", "тренд вверх, но overbought (RSI 70) — ждать pullback."),
            _v("NEAR", "LONG")]
    kept, waits = collapse_waits_when_entries(rows)
    assert [r[0] for r in kept] == ["NEAR"]
    assert waits and "BTC" in waits and "ETH" in waits


def test_wait_reason_is_summarised_not_repeated():
    rows = [_v("BTC", "WAIT", "тренд вверх, но overbought (RSI 70) — ждать pullback."),
            _v("ONDO", "WAIT", "коррекция в восходящем тренде — слабый сигнал."),
            _v("NEAR", "LONG")]
    _, waits = collapse_waits_when_entries(rows)
    assert "перегрев" in waits
    assert "слабый" in waits
    assert waits.count("RSI") <= 1


def test_no_entries_means_no_collapse_here():
    """Когда входов нет, работает прежняя сводка «Входов нет (9/9)»."""
    rows = [_v("BTC", "WAIT"), _v("ETH", "WAIT")]
    kept, waits = collapse_waits_when_entries(rows)
    assert kept == rows
    assert waits == ""


def test_nodata_is_never_hidden():
    rows = [_v("TAO", "NODATA", "нет данных"), _v("NEAR", "LONG")]
    kept, _ = collapse_waits_when_entries(rows)
    assert any(r[0] == "TAO" for r in kept)


# ------------------------------------------------- 3. приоритет входов

def test_entries_ranked_by_relative_strength():
    rows = [_v("NEAR", "LONG", rs=-8.8), _v("TAO", "LONG", rs=1.8),
            _v("MORPHO", "LONG", rs=19.6)]
    assert [r[0] for r in rank_entries(rows)] == ["MORPHO", "TAO", "NEAR"]


def test_entries_without_rs_go_last():
    rows = [_v("NEAR", "LONG", rs=None), _v("TAO", "LONG", rs=1.8)]
    assert [r[0] for r in rank_entries(rows)] == ["TAO", "NEAR"]


def test_ranking_is_stable_without_any_rs():
    rows = [_v("NEAR", "LONG"), _v("TAO", "LONG")]
    assert [r[0] for r in rank_entries(rows)] == ["NEAR", "TAO"]


def test_single_entry_needs_no_ranking():
    rows = [_v("NEAR", "LONG", rs=5.0)]
    assert rank_entries(rows) == rows


# ------------------------------- 2. план входа: стоп и размер, безопасно

def test_stop_on_wrong_side_is_never_printed():
    """Стоп выше входа для LONG — не стоп, а мгновенный убыток.

    Поймано на превью 30.08: при рассогласованных данных функция печатала
    «стоп 128» при цене 96. В проде такого быть не должно, но печатать
    оператору неисполнимый план опаснее, чем не печатать ничего."""
    from src.whitelist_focus import _plan_line

    assert _plan_line("LONG", entry=100.0, sl=128.0, n_entries=1) == ""
    assert _plan_line("SHORT", entry=100.0, sl=80.0, n_entries=1) == ""


def test_valid_stop_is_printed():
    from src.whitelist_focus import _plan_line

    out = _plan_line("LONG", entry=100.0, sl=94.0, n_entries=1)
    assert "94" in out and "6.0%" in out


def test_size_is_divided_between_simultaneous_entries():
    """Четыре входа в одну сторону — одна ставка; размер делится.

    Иначе предупреждение «дели размер» противоречит числу рядом с ним."""
    from src.whitelist_focus import _plan_line

    one = _plan_line("LONG", entry=100.0, sl=90.0, n_entries=1)
    four = _plan_line("LONG", entry=100.0, sl=90.0, n_entries=4)
    def pct(s):
        import re
        m = re.search(r"размер ~([\d.]+)%", s)
        return float(m.group(1)) if m else None
    assert pct(one) and pct(four)
    assert pct(four) < pct(one)
    assert abs(pct(four) - pct(one) / 4) < 0.6


def test_degenerate_inputs_print_nothing():
    from src.whitelist_focus import _plan_line

    assert _plan_line("LONG", entry=0.0, sl=1.0, n_entries=1) == ""
    assert _plan_line("WAIT", entry=100.0, sl=90.0, n_entries=1) == ""
