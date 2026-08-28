"""Каскад в письме детектора (28.08.2026).

Пришедшее письмо — 13 предупреждений на одну причину: замолчал
`whitelist_focus`, и следом отдельными строками отчитались два поля и все
девять монет, которые он пишет. Читателю это подаётся как тринадцать
проблем, хотя проблема одна, а остальное — её следствие.

Детектор, который на один отказ выдаёт экран текста, перестают читать. Это
та же болезнь, что была у дайджеста с восемью «НЕ ВХОДИТЬ» (политика §7.6):
перечисляй причину, а не её последствия.

Поля и монеты проверяются по-прежнему — но отдельными строками выходят
только тогда, когда источники живы. Если источник молчит, его отсутствие и
есть ответ.
"""
from datetime import datetime, timedelta, timezone

from src.journal_health import run_checks

NOW = datetime(2026, 8, 28, 0, 41, tzinfo=timezone.utc)
COINS = ("BTC", "ETH", "ZEC")


def _e(hours_ago, source="whitelist_focus", coin="BTC", **extra):
    rec = {"ts": (NOW - timedelta(hours=hours_ago)).isoformat(),
           "source": source, "coin": coin, "verdict": "WAIT",
           "verdict_raw": "WAIT", "rs_30d": 1.0}
    rec.update(extra)
    return rec


def _healthy():
    return [_e(1, coin=c) for c in COINS] + [_e(1, "daily_monitor")]


def test_healthy_journal_is_silent():
    assert run_checks(_healthy(), NOW, COINS) == []


def test_dead_source_reports_once_not_thirteen_times():
    """Ровно письмо 28.08: один отказ, тринадцать строк."""
    entries = [_e(41, coin=c) for c in COINS] + [_e(1, "daily_monitor")]
    issues = run_checks(entries, NOW, COINS)
    assert len(issues) <= 2, [i.message for i in issues]
    assert any("whitelist_focus" in i.message for i in issues)


def test_consequence_is_named_but_not_enumerated():
    entries = [_e(41, coin=c) for c in COINS] + [_e(1, "daily_monitor")]
    text = " ".join(i.message for i in run_checks(entries, NOW, COINS))
    # монеты и поля по отдельности не перечисляются
    assert "монета BTC" not in text
    assert "поле rs_30d" not in text
    # но сказано, что сбор встал
    assert "не собираются" in text or "не копятся" in text


def test_field_gap_still_reported_when_sources_alive():
    """Источник жив, а поле перестало писаться — это отдельная поломка."""
    entries = [_e(1, coin=c, verdict_raw=None, rs_30d=None) for c in COINS]
    entries += [_e(200, coin="BTC")]          # старая запись с полями
    # daily_monitor тоже без полей: иначе он один поддерживает их свежесть
    entries += [_e(1, "daily_monitor", verdict_raw=None, rs_30d=None)]
    text = " ".join(i.message for i in run_checks(entries, NOW, COINS))
    assert "verdict_raw" in text or "rs_30d" in text


def test_missing_coin_still_reported_when_sources_alive():
    entries = [_e(1, coin="BTC"), _e(1, "daily_monitor")]
    text = " ".join(i.message for i in run_checks(entries, NOW, ("BTC", "ONDO")))
    assert "ONDO" in text


def test_empty_journal_is_still_one_critical_line():
    issues = run_checks([], NOW, COINS)
    assert len(issues) == 1
    assert issues[0].severity == "critical"
