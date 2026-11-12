"""Упавший прогон по расписанию — не тихий (12.11.2026, аудит).

За два месяца в Actions упало 20 прогонов по расписанию, и ни об одном
канал не узнал: whale-monitor 10 раз (файл > 100 МБ, состояние китов не
сохранялось), operator-edge 9 воскресений подряд. С 01.10 киты в канал не
пишут — их отказ стал невидим. Детектор журнала следит за журналом
вердиктов, а не за тем, живы ли сами прогоны.
"""
from datetime import datetime, timedelta, timezone

from src.actions_health import failed_runs, render

NOW = datetime(2026, 11, 2, 16, 0, tzinfo=timezone.utc)


def _run(name, hours_ago, rid, conclusion="failure", event="schedule"):
    return {"id": rid, "name": name, "conclusion": conclusion, "event": event,
            "created_at": (NOW - timedelta(hours=hours_ago)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")}


def _jobs(steps):
    return lambda rid: [{"steps": [{}] * steps}]


def test_counts_scheduled_failures_in_window():
    runs = [_run("whale-monitor", 2, 1), _run("whale-monitor", 20, 2),
            _run("operator-edge", 3, 3), _run("whale-monitor", 50, 4)]
    assert failed_runs(runs, _jobs(5), NOW) == {"whale-monitor": 2,
                                                "operator-edge": 1}


def test_runner_not_acquired_is_not_our_failure():
    """09.11 heartbeat: «job was not acquired by Runner» — ноль шагов."""
    assert failed_runs([_run("heartbeat", 2, 1)], _jobs(0), NOW) == {}


def test_manual_and_push_runs_are_ignored():
    runs = [_run("momentum-sweep", 1, 1, event="workflow_dispatch"),
            _run("tests", 1, 2, event="push")]
    assert failed_runs(runs, _jobs(5), NOW) == {}


def test_own_workflow_is_not_echoed():
    assert failed_runs([_run("journal-health", 1, 1)], _jobs(5), NOW) == {}


def test_success_is_not_counted():
    assert failed_runs([_run("whale-monitor", 1, 1, conclusion="success")],
                       _jobs(5), NOW) == {}


def test_render_names_workflows_or_stays_silent():
    assert render({}) is None
    msg = render({"whale-monitor": 2, "operator-edge": 1})
    assert "whale-monitor ×2" in msg and "operator-edge" in msg
    assert "operator-edge ×" not in msg


def test_run_without_any_jobs_is_infra_too():
    """heartbeat 09.11: у прогона нет заданий вовсе — тоже отказ GitHub."""
    assert failed_runs([_run("heartbeat", 2, 1)], lambda rid: [], NOW) == {}


def test_runs_after_now_are_outside_window():
    assert failed_runs([_run("whale-monitor", -5, 1)], _jobs(5), NOW) == {}
