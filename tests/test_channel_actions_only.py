"""В канал — только «что делать» (01.10.2026, решение оператора).

Оператор: «в сообщениях 99% шум… киты мне нужны только для того, что
делать, их отдельная активность меня не интересует». За 01.10 в канал
пришли сводка китов, «Стечение признаков», «Затухание китового сигнала» —
ни в одном нет действия.

Заглушённые виды не теряются: текст печатается в лог Actions, сбор и
расчёты идут как прежде. Канал о них просто не слышит, и маркер
«канал говорил» не обновляется — иначе heartbeat считал бы молчаливый
день разговорчивым.
"""
import importlib
import pathlib
import re

import src.telegram_sender as tg

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_muted_kind_is_logged_not_sent(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(tg, "send_messages", lambda m: sent.extend(m))
    tg.muted_sender("whales")(["🐋 Whale digest"])
    assert sent == []
    assert "Whale digest" in capsys.readouterr().out


def test_action_kind_goes_to_channel(monkeypatch):
    sent = []
    monkeypatch.setattr(tg, "send_messages", lambda m: sent.extend(m))
    tg.muted_sender("action")(["🔴 ЗАКРОЙ"])
    assert sent == ["🔴 ЗАКРОЙ"]


def test_whales_and_research_are_muted():
    assert {"whales", "research"} <= tg.MUTED_KINDS
    assert "action" not in tg.MUTED_KINDS


def test_whale_monitor_sends_through_muted_whales():
    wm = importlib.import_module("src.whale_monitor")
    assert getattr(wm.send_messages, "kind", None) == "whales"


# Исследовательские раннеры: ни один не шлёт в канал напрямую.
RESEARCH = ["barrier_run", "operator_edge_run", "vol_premium_history",
            "momentum_sweep_run", "pump_fade_run", "aevo_probe",
            "confluence_run", "whale_delay_run", "ratio_probe",
            "vol_premium_run", "tactical_backtest_run", "positioning_run",
            "binance_probe"]


def test_research_runners_do_not_reach_channel():
    bad = []
    for name in RESEARCH:
        src = (ROOT / "scripts" / f"{name}.py").read_text()
        if re.search(r"from src\.telegram_sender import send_messages\b", src):
            bad.append(name)
        elif 'muted_sender("research")' not in src:
            bad.append(name + " (нет muted_sender)")
    assert bad == [], bad


def test_research_modules_in_src_do_not_reach_channel():
    for name in ("signal_backtester_runner", "weekly_kpi"):
        src = (ROOT / "src" / f"{name}.py").read_text()
        assert 'muted_sender("research")' in src, name


def test_failure_detector_still_speaks():
    """Детектор тихого отказа — не шум: поломка сообщается всегда (§4)."""
    src = (ROOT / "scripts" / "journal_health_alert.py").read_text()
    assert "muted_sender" not in src


def test_whitelist_daily_has_no_whale_activity_line(tmp_path):
    from src.whitelist_focus import render_whitelist_verdicts
    import inspect
    src = inspect.getsource(render_whitelist_verdicts)
    assert "lines.append(stance_line)" not in src
