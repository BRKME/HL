"""Одна монета — одна запись на момент времени (23.08.2026).

Монета, которую оператор держит, журналилась ДВАЖДЫ за один прогон: путём
позиции и путём дайджеста, с одинаковым ts. 14 таких пар накопилось с 03.08
— по одной в день. Та же передискретизация, что была у китовых сигналов:
n растёт, независимой информации не прибавляется.
"""
from datetime import datetime, timedelta, timezone

from src.verdict_journal import VerdictEntry, append_verdicts, load_verdicts

NOW = datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc)


def _e(coin, ts=NOW, source="whitelist_focus"):
    return VerdictEntry(ts=ts, source=source, coin=coin, mark=1.0,
                        verdict="WAIT", rationale="тест")


def test_duplicate_within_batch_is_dropped(tmp_path):
    p = tmp_path / "j.jsonl"
    assert append_verdicts(p, [_e("BTC"), _e("BTC")]) == 1


def test_duplicate_across_calls_is_dropped(tmp_path):
    """Путь позиции и путь дайджеста в одном прогоне — ровно этот случай."""
    p = tmp_path / "j.jsonl"
    append_verdicts(p, [_e("BTC", source="daily_monitor")])
    assert append_verdicts(p, [_e("BTC", source="whitelist_focus")]) == 0
    assert len(load_verdicts(p)) == 1


def test_different_coins_both_written(tmp_path):
    p = tmp_path / "j.jsonl"
    assert append_verdicts(p, [_e("BTC"), _e("ETH")]) == 2


def test_same_coin_later_tick_is_written(tmp_path):
    p = tmp_path / "j.jsonl"
    append_verdicts(p, [_e("BTC")])
    assert append_verdicts(p, [_e("BTC", ts=NOW + timedelta(hours=2))]) == 1


def test_empty_batch(tmp_path):
    assert append_verdicts(tmp_path / "j.jsonl", []) == 0


def test_missing_file_does_not_break_dedupe(tmp_path):
    assert append_verdicts(tmp_path / "nested" / "j.jsonl", [_e("BTC")]) == 1
