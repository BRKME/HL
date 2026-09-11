"""Очистка буфера дайджеста китов (11.09.2026).

Оператор прислал ТРИ одинаковых письма подряд — 9, 10 и 11 сентября, с
теми же монетами, суммами и множителями, каждое с заголовком «за 24ч».

Причина: `_clear_pending` УДАЛЯЛ файл буфера. Воркфлоу делает `git add`
только для существующих файлов, поэтому исчезновение в индекс не попадало,
и файл возвращался из репозитория при следующем checkout. Шесть сигналов,
накопленных с 21.08 по 04.09, рассылались каждый день как свежие.

Отметка «дайджест отправлен» при этом обновлялась исправно — по ней всё
выглядело здоровым. Симптом был виден только в самих письмах.
"""
import json
from pathlib import Path

import pytest

from src.whale_monitor import _append_pending, _clear_pending, _read_pending


class _Sig:
    """Минимальный сигнал. severity — ЧИСЛО: чтение приводит его к int, и
    строка «info» молча отбраковывала запись при обратном разборе."""

    def __init__(self, coin="HYPE"):
        self.coin = coin
        self.rule = "WHALE_NEW_OPEN"
        self.severity = 1
        self.message = "тест"
        self.details = {}


def test_clear_empties_but_keeps_the_file(tmp_path):
    """Ключевое: файл обязан ОСТАТЬСЯ, иначе очистка не переживёт Actions."""
    p = tmp_path / "pending.jsonl"
    p.write_text('{"coin": "HYPE"}\n')
    _clear_pending(p)
    assert p.exists(), "файл удалён — очистка не попадёт в git add"
    assert p.read_text() == ""


def test_cleared_buffer_reads_as_empty(tmp_path):
    p = tmp_path / "pending.jsonl"
    p.write_text('{"coin": "HYPE"}\n')
    _clear_pending(p)
    assert _read_pending(p) == []


def test_clear_on_missing_file_creates_empty(tmp_path):
    p = tmp_path / "nested" / "pending.jsonl"
    _clear_pending(p)
    assert p.exists() and p.read_text() == ""


def test_append_after_clear_works(tmp_path):
    from datetime import datetime, timezone

    p = tmp_path / "pending.jsonl"
    _clear_pending(p)
    _append_pending([_Sig()], p, run_ts=datetime(2026, 9, 11, tzinfo=timezone.utc))
    assert len(_read_pending(p)) == 1


def test_same_signals_not_resent_after_clear(tmp_path):
    """Ровно симптом 09–11.09: один и тот же набор три дня подряд."""
    from datetime import datetime, timezone

    p = tmp_path / "pending.jsonl"
    _append_pending([_Sig(), _Sig("ZEC")], p,
                    run_ts=datetime(2026, 9, 11, tzinfo=timezone.utc))
    assert len(_read_pending(p)) == 2
    _clear_pending(p)
    assert _read_pending(p) == []
