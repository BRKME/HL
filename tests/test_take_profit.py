"""Тактический сигнал должен давать не только SL (где резать убыток), но и TP
(где фиксировать прибыль). Оператор: 'не понятна граница где выходить по
профиту'. TP привязан к расстоянию до SL через R:R, чтобы цель была осмысленной.
"""
from src.tactical_signals import tp_for, sl_for


def test_tp_short_below_entry():
    # SHORT: прибыль когда цена падает -> TP НИЖЕ входа
    entry = 1582.0
    sl = 1755.0          # SL выше входа (риск 173)
    tp = tp_for("SHORT", entry, sl, rr=1.5)
    assert tp is not None
    assert tp < entry            # цель ниже входа
    # R:R 1.5 -> прибыль = 1.5 × риск = 1.5 × 173 ≈ 260 -> tp ≈ 1582-260 = 1322
    assert abs(tp - (entry - 1.5 * (sl - entry))) < 1


def test_tp_long_above_entry():
    entry = 100.0
    sl = 90.0            # риск 10
    tp = tp_for("LONG", entry, sl, rr=2.0)
    assert tp > entry
    assert abs(tp - (entry + 2.0 * (entry - sl))) < 0.01   # 100 + 20 = 120


def test_tp_none_without_sl():
    assert tp_for("SHORT", 1582, None, rr=1.5) is None
    assert tp_for("SHORT", 1582, 1582, rr=1.5) is None     # нулевой риск


def test_tp_in_alert_text():
    from src.tactical_signals import build_alert
    msg = build_alert(coin="ETH", direction="SHORT", entry=1582.0, sl=1755.0,
                      tp=1322.0, rationale="тренд вниз", funding_apr_pct=-1.7,
                      whale_note="киты: нет данных", regime="BEAR")
    assert "TP" in msg or "тейк" in msg.lower() or "цель" in msg.lower()
    assert "1,322" in msg or "1322" in msg
