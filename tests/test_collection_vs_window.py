"""Сбор данных отвязан от окна отправки (27.08.2026).

Лог Actions: `16:02 UTC → No open positions — skipping report. / no verdict
journal changes to commit`. Воркфлоу отработал штатно и не сделал ничего:
16:02 вне окна 06–14 UTC, поэтому не ушёл дайджест — и вместе с ним НЕ
СОБРАЛИСЬ ДАННЫЕ. День потерян целиком.

Причина — не окно, а то, что к нему привязаны две разные задачи. Окно
существует ради оператора: сводка «что покупать сегодня» бессмысленна
ночью. У сбора данных такого ограничения нет вовсе: журнал нужен всегда,
любой прогон дня годится.

GitHub Actions при нагрузке пропускает запуски по расписанию. Из восьми
слотов daily-monitor в окно попадали четыре; выпали они — потерян день
наблюдений, которые нужны для чекпойнтов.

Теперь: данные собираются на ЛЮБОМ прогоне дня, сообщение уходит только в
окне. Пропуск дневных слотов стоит сообщения, но не данных.
"""
from datetime import datetime, timezone

import pytest


def _t(hour, day=27):
    return datetime(2026, 8, day, hour, 0, tzinfo=timezone.utc)


def test_data_collected_outside_window(monkeypatch, tmp_path):
    """Ровно случай 27.08: единственный прогон дня в 16:02."""
    import src.daily_monitor as dm

    sent, journaled = [], []
    monkeypatch.setattr(dm, "send_messages", lambda m: sent.extend(m))
    monkeypatch.setattr(dm, "_render_flat_digest",
                        lambda now, acc: journaled.append(now) or "🎯 сводка")

    dm._flat_digest_once_a_day(_t(16), [], tmp_path)
    assert journaled, "данные обязаны собраться даже вне окна"
    assert sent == [], "сообщение вне окна не уходит"


def test_message_sent_inside_window(monkeypatch, tmp_path):
    import src.daily_monitor as dm

    sent = []
    monkeypatch.setattr(dm, "send_messages", lambda m: sent.extend(m))
    monkeypatch.setattr(dm, "_render_flat_digest",
                        lambda now, acc: "🎯 сводка")

    dm._flat_digest_once_a_day(_t(9), [], tmp_path)
    assert sent == ["🎯 сводка"]


def test_collection_happens_once_per_day(monkeypatch, tmp_path):
    import src.daily_monitor as dm

    calls = []
    monkeypatch.setattr(dm, "send_messages", lambda m: None)
    monkeypatch.setattr(dm, "_render_flat_digest",
                        lambda now, acc: calls.append(now) or "🎯 сводка")

    for hour in (7, 9, 16, 19):
        dm._flat_digest_once_a_day(_t(hour), [], tmp_path)
    assert len(calls) == 1


def test_late_collection_does_not_block_next_day(monkeypatch, tmp_path):
    import src.daily_monitor as dm

    calls = []
    monkeypatch.setattr(dm, "send_messages", lambda m: None)
    monkeypatch.setattr(dm, "_render_flat_digest",
                        lambda now, acc: calls.append(now) or "🎯 сводка")

    dm._flat_digest_once_a_day(_t(16, day=27), [], tmp_path)
    dm._flat_digest_once_a_day(_t(9, day=28), [], tmp_path)
    assert len(calls) == 2


def test_night_run_collects_but_stays_silent(monkeypatch, tmp_path):
    """Ночной прогон данные пишет, оператора не будит."""
    import src.daily_monitor as dm

    sent, calls = [], []
    monkeypatch.setattr(dm, "send_messages", lambda m: sent.extend(m))
    monkeypatch.setattr(dm, "_render_flat_digest",
                        lambda now, acc: calls.append(now) or "🎯 сводка")

    dm._flat_digest_once_a_day(_t(2), [], tmp_path)
    assert calls and sent == []


def test_send_failure_keeps_data(monkeypatch, tmp_path):
    """Упавший Telegram не должен отменять уже собранные данные."""
    import src.daily_monitor as dm

    calls = []

    def boom(_):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(dm, "send_messages", boom)
    monkeypatch.setattr(dm, "_render_flat_digest",
                        lambda now, acc: calls.append(now) or "🎯 сводка")

    dm._flat_digest_once_a_day(_t(9), [], tmp_path)
    assert calls
