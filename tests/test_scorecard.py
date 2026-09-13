"""Табель системы вместо повтора списка монет (13.09.2026).

Замечание оператора: heartbeat «просто дублирует предыдущее без части
информации». Верно — он перечислял те же монеты, что дайджест, только с
меньшим набором данных.

Но одно он знает, чего не знает дайджест: результат УЖЕ ВЫДАННЫХ сигналов
с момента входа. В тот день это было 1 из 7 в плюсе при среднем −3.7%.
Такое письмо стоит получать; список монет, повторяющий дайджест, — нет.

«Бот жив» убрано: детектор журнала кричит при молчании источника, а сам
факт прихода письма и есть доказательство живости.
"""
import pytest

from src.heartbeat import scorecard


def _t(coin, entry, cur, side="LONG"):
    return {"coin": coin, "entry": entry, "current": cur, "direction": side}


def test_reports_share_in_profit():
    """Ровно случай 13.09: один в плюсе из семи."""
    trades = [_t("ASTER", 0.7672, 0.6945), _t("BTC", 79505, 76650),
              _t("ETH", 2433, 2472), _t("HYPE", 81.93, 77.56),
              _t("NEAR", 2.428, 2.302), _t("TAO", 236, 233.7),
              _t("ZEC", 1124, 1090)]
    out = scorecard(trades)
    assert "1 из 7" in out
    assert "-3." in out                      # средний около -3.7%


def test_names_best_and_worst():
    out = scorecard([_t("A", 100, 90), _t("B", 100, 110)])
    assert "лучший B" in out
    assert "худший A" in out


def test_short_side_is_inverted():
    out = scorecard([_t("A", 100, 90, side="SHORT")])
    assert "+10.0%" in out


def test_green_when_average_positive():
    assert scorecard([_t("A", 100, 110)]).startswith("🟢")


def test_red_when_average_clearly_negative():
    assert scorecard([_t("A", 100, 90)]).startswith("🔴")


def test_no_trades_no_message():
    assert scorecard([]) is None
    assert scorecard(None) is None


def test_broken_rows_skipped():
    assert scorecard([{"coin": "A"}, _t("B", 100, 110)]) is not None
