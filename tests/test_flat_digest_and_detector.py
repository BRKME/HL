"""Три дефекта, найденные 21.08.2026 по жалобе «никаких предложений».

Оператор три дня был вне рынка. За эти три дня рынок вырос на +15…+29% по
всем девяти монетам, и ни одного предложения о входе не пришло.

**1. Маркер суточного сбора не коммитился.** `verdict_collection.json` не
попал в `git add` воркфлоу — в отличие от `morning_digest.json`. В Actions
рабочая директория прогон не переживает, поэтому маркер терялся, и сбор шёл
на каждом тике окна: 36 записей в день вместо 9. Это ровно то правило,
которое я сам записал в политику §4.3 и сам же нарушил через сутки.

**2. Детектор кричал на законную тишину.** «источник daily_monitor молчит
2 дн» — но `daily_monitor` пишет вердикты только по ОТКРЫТЫМ позициям, а
позиций не было. Молчание было правдой о портфеле, а не об отказе. Ложная
тревога обесценивает детектор быстрее, чем пропущенная.

**3. Дайджест не уходил при пустом портфеле.** Решение оператора 13.06:
«без позиций сообщение бесполезно». Три дня без единого предложения при
росте на 20% показали обратное — именно вне рынка сводка «что покупать»
нужнее всего. Решение отменено по жалобе оператора 21.08.
"""
from datetime import datetime, timedelta, timezone

from src.journal_health import (
    CONDITIONAL_SOURCES,
    check_source_staleness,
)

NOW = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)


def _e(hours_ago, source, coin="BTC"):
    return {"ts": (NOW - timedelta(hours=hours_ago)).isoformat(),
            "source": source, "coin": coin, "verdict": "WAIT"}


# ------------------------------------------- 2. детектор и законная тишина

def test_daily_monitor_is_conditional():
    assert "daily_monitor" in CONDITIONAL_SOURCES


def test_conditional_source_silence_alone_is_not_a_fault():
    """Ровно ложная тревога 21.08: позиций нет — писать нечего."""
    entries = [_e(1, "whitelist_focus"), _e(24 * 2, "daily_monitor")]
    assert check_source_staleness(entries, NOW) == []


def test_unconditional_source_silence_still_warns():
    entries = [_e(24 * 2, "whitelist_focus"), _e(1, "daily_monitor")]
    issues = check_source_staleness(entries, NOW)
    assert len(issues) == 1
    assert "whitelist_focus" in issues[0].message


def test_everything_silent_still_warns():
    """Если замолчали все, тишина условного источника уже не оправдание."""
    entries = [_e(24 * 3, "whitelist_focus"), _e(24 * 3, "daily_monitor")]
    msgs = " ".join(i.message for i in check_source_staleness(entries, NOW))
    assert "whitelist_focus" in msgs
    assert "daily_monitor" in msgs


def test_fresh_sources_are_quiet():
    entries = [_e(1, "whitelist_focus"), _e(2, "daily_monitor")]
    assert check_source_staleness(entries, NOW) == []


# ------------------------------- 3. дайджест уходит и при пустом портфеле

def test_digest_is_sent_when_flat(monkeypatch, tmp_path):
    import src.daily_monitor as dm

    sent = []
    monkeypatch.setattr(dm, "send_messages", lambda m: sent.extend(m))
    monkeypatch.setattr(dm, "STATE_DIR", tmp_path)
    monkeypatch.setattr(dm, "_render_flat_digest",
                        lambda now, accounts: "🎯 Whitelist daily — тест",
                        raising=False)

    dm._flat_digest_once_a_day(datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc),
                               [], tmp_path)
    assert sent and "Whitelist daily" in sent[0]


def test_flat_digest_sent_once_per_day(monkeypatch, tmp_path):
    import src.daily_monitor as dm

    sent = []
    monkeypatch.setattr(dm, "send_messages", lambda m: sent.extend(m))
    monkeypatch.setattr(dm, "_render_flat_digest",
                        lambda now, accounts: "🎯 дайджест", raising=False)

    for hour in (7, 9, 11, 13):
        dm._flat_digest_once_a_day(
            datetime(2026, 8, 21, hour, 0, tzinfo=timezone.utc), [], tmp_path)
    assert len(sent) == 1


def test_flat_digest_silent_outside_window(monkeypatch, tmp_path):
    import src.daily_monitor as dm

    sent = []
    monkeypatch.setattr(dm, "send_messages", lambda m: sent.extend(m))
    monkeypatch.setattr(dm, "_render_flat_digest",
                        lambda now, accounts: "🎯 дайджест", raising=False)

    dm._flat_digest_once_a_day(
        datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc), [], tmp_path)
    assert sent == []


def test_send_failure_does_not_burn_the_day(monkeypatch, tmp_path):
    import src.daily_monitor as dm
    from src.morning_gate import should_run_digest

    def boom(_):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(dm, "send_messages", boom)
    monkeypatch.setattr(dm, "_render_flat_digest",
                        lambda now, accounts: "🎯 дайджест", raising=False)

    dm._flat_digest_once_a_day(
        datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc), [], tmp_path)
    assert should_run_digest(
        datetime(2026, 8, 21, 11, 0, tzinfo=timezone.utc),
        tmp_path, name=dm.VERDICT_COLLECTION_MARKER) is True
