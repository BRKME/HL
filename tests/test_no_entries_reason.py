"""«Сегодня ничего не делаем» — с причиной (29.09.2026).

«⚪ Входов нет (9/9)» сообщало, ЧТО входов нет, но не ПОЧЕМУ. Оператор
спросил: а почему не шорты? Ответ система уже посчитала — график по
каждой монете (verdict_raw) и режим. Здесь только показ: вердикты не
меняются.
"""
from src.digest_compact import collapse_wait_verdicts


def _v(coin, verdict="WAIT", raw="WAIT"):
    return (coin, 1.0, verdict, "r", raw, "r")


def test_day_of_29_09_names_the_conflict():
    """Ровно 29.09: график вверх у восьми, режим BEAR их гасит, NEAR без
    тренда, шортов график не даёт нигде."""
    rows = [_v(c, raw="LONG") for c in
            ("BTC", "ETH", "ZEC", "HYPE", "ASTER", "MORPHO", "TAO", "ONDO")]
    rows.append(_v("NEAR", raw="WAIT"))
    _, summary = collapse_wait_verdicts(rows, regime="BEAR")
    assert "Сегодня ничего не делаем" in summary
    assert "Входов нет (9/9)" in summary
    assert "график вверх у 8" in summary
    assert "режим BEAR" in summary
    assert "без тренда: <code>NEAR</code>" in summary
    assert "шортов нет" in summary


def test_short_blocked_by_bull_regime():
    rows = [_v("BTC", raw="SHORT"), _v("ETH", raw="WAIT")]
    _, summary = collapse_wait_verdicts(rows, regime="BULL")
    assert "график вниз у 1" in summary
    assert "режим BULL" in summary
    assert "шортов нет" not in summary
    assert "лонгов нет" in summary


def test_long_held_back_not_by_regime():
    """График вверх, режим не против — вход отложил другой фильтр
    (перегрев и т.п.). Режим в этом не обвиняем."""
    rows = [_v("BTC", raw="LONG")]
    _, summary = collapse_wait_verdicts(rows, regime="BULL")
    assert "график вверх у 1" in summary
    assert "режим" not in summary.split("график вверх")[1].split("\n")[0]


def test_without_regime_still_explains_chart():
    rows = [_v("BTC", raw="WAIT"), _v("ETH", raw="WAIT")]
    _, summary = collapse_wait_verdicts(rows)
    assert "без тренда" in summary
    assert "Входов нет (2/2)" in summary
