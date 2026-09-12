"""Дедуп заполнений китов по времени, а не по идентификатору (12.09.2026).

Сбор обвалился: до 3 сентября тысячи заполнений в день, после — единицы.
Правок в китовом коде не было; причина в данных.

Идентификаторы сделок НЕ монотонны по времени: 34 483 нарушения из 120 294
заполнений, почти треть. Трекер отбрасывал всё с `tid <= last_tid`, поэтому
один высокий идентификатор НАВСЕГДА закрывал для кита все последующие
сделки с меньшими номерами.

Время монотонно по определению. Идентификаторы остаются нужны для границы
окна: внутри одной метки времени бывает несколько сделок, и без них повторы
на стыке прошли бы дважды.
"""
import pytest

from src.whale_tracker import FillCursor


def test_cursor_tracks_time_not_only_tid():
    c = FillCursor()
    c.advance_time("0xaa", 1000, [7])
    assert c.last_time_ms("0xaa") == 1000
    assert c.recent_tids("0xaa") == {7}


def test_lower_tid_at_later_time_is_not_lost():
    """Ровно причина обвала: tid меньше, а время больше — заполнение
    обязано пройти."""
    c = FillCursor()
    c.advance_time("0xaa", 1000, [99999])
    # следующая сделка позже по времени, но с МЕНЬШИМ идентификатором
    assert 500 not in c.recent_tids("0xaa")
    assert c.last_time_ms("0xaa") < 2000


def test_boundary_tids_accumulate_within_same_timestamp():
    """Несколько сделок в одну метку времени — все идентификаторы нужны."""
    c = FillCursor()
    c.advance_time("0xaa", 1000, [1])
    c.advance_time("0xaa", 1000, [2])
    assert c.recent_tids("0xaa") == {1, 2}


def test_boundary_tids_reset_on_newer_timestamp():
    """При переходе к новой метке старые идентификаторы не нужны — иначе
    набор рос бы без предела."""
    c = FillCursor()
    c.advance_time("0xaa", 1000, [1, 2])
    c.advance_time("0xaa", 2000, [3])
    assert c.recent_tids("0xaa") == {3}


def test_older_timestamp_does_not_move_cursor_back():
    c = FillCursor()
    c.advance_time("0xaa", 2000, [5])
    c.advance_time("0xaa", 1000, [4])
    assert c.last_time_ms("0xaa") == 2000


def test_unknown_whale_starts_from_zero():
    c = FillCursor()
    assert c.last_time_ms("0xzz") == 0
    assert c.recent_tids("0xzz") == set()


def test_old_tid_cursor_still_readable():
    """Старые курсоры в состоянии содержат только tid: переход не должен
    ронять прогон."""
    c = FillCursor(last_tid_by_whale={"0xaa": 123})
    assert c.last_tid("0xaa") == 123
    assert c.last_time_ms("0xaa") == 0
