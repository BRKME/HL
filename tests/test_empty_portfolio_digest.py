"""Передискретизация журнала вердиктов (08.08.2026).

Разбор сообщения от 22:29. Утром позиций не было, и тихое журналирование
сработало на КАЖДОМ тике: 40 записей за 08.08 вместо 8 — пять наблюдений
одного и того же суточного вердикта по каждой монете.

Это ровно тот дефект, что был у китовых сигналов (политика §5): n растёт,
независимой информации не прибавляется. К разбору 23.08 выборка оказалась бы
раздутой в разы, а сравнение raw-vs-final считалось бы по повторам.

Отдельно проверено и НЕ изменено: дайджест при пустом портфеле не шлётся —
решение оператора от 13.06, в коде стоит явная пометка. Сначала я принял это
за регресс; это не регресс.

Маркер сбора отдельный от маркера дайджеста: пустой портфель утром не должен
лишать оператора whitelist-блока в отчёте, если позиции откроются днём.
"""
from datetime import datetime, timezone

from src.daily_monitor import VERDICT_COLLECTION_MARKER
from src.morning_gate import STATE_FILE, mark_digest_done, should_run_digest


def _t(day, hour):
    return datetime(2026, 8, day, hour, 0, tzinfo=timezone.utc)


def test_collection_happens_once_per_day(tmp_path):
    """Пять тиков подряд — одна порция наблюдений, а не пять."""
    done = 0
    for hour in (7, 9, 11, 13, 14):
        if should_run_digest(_t(8, hour), tmp_path, name=VERDICT_COLLECTION_MARKER):
            done += 1
            mark_digest_done(_t(8, hour), tmp_path, name=VERDICT_COLLECTION_MARKER)
    assert done == 1


def test_collection_resumes_next_day(tmp_path):
    mark_digest_done(_t(8, 9), tmp_path, name=VERDICT_COLLECTION_MARKER)
    assert should_run_digest(_t(9, 9), tmp_path,
                             name=VERDICT_COLLECTION_MARKER) is True


def test_collection_marker_is_separate_from_digest(tmp_path):
    """Сбор при пустом портфеле не должен гасить дайджест в отчёте."""
    mark_digest_done(_t(8, 9), tmp_path, name=VERDICT_COLLECTION_MARKER)
    assert should_run_digest(_t(8, 11), tmp_path) is True
    assert (tmp_path / VERDICT_COLLECTION_MARKER).exists()
    assert not (tmp_path / STATE_FILE).exists()


def test_digest_marker_does_not_block_collection(tmp_path):
    mark_digest_done(_t(8, 9), tmp_path)
    assert should_run_digest(_t(8, 11), tmp_path,
                             name=VERDICT_COLLECTION_MARKER) is True


def test_collection_respects_the_same_window(tmp_path):
    for hour in (2, 5, 16, 19, 22):
        assert should_run_digest(_t(8, hour), tmp_path,
                                 name=VERDICT_COLLECTION_MARKER) is False
