"""Упавшие прогоны GitHub Actions — в канал (12.11.2026, аудит).

Доктрина тихого отказа (§4) покрывала журнал вердиктов, но не сами
прогоны. За два месяца упало 20 прогонов по расписанию — whale-monitor
10 раз (файл > 100 МБ), operator-edge 9 воскресений подряд — и канал не
узнал ни об одном: с 01.10 киты в канал не пишут, их отказ невидим.

Считаются только прогоны ПО РАСПИСАНИЮ: ручной запуск и пуш видит тот,
кто их сделал. Прогон, которому GitHub не выдал раннер (ноль шагов), —
не наш отказ и не действие для оператора.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Sequence

# Детектор ходит раз в сутки, но Actions сдвигает его на 3–7 часов: окно в
# 24 ч оставляло бы дыры между соседними прогонами.
WINDOW_H = 30
OWN_WORKFLOW = "journal-health"     # своё падение не пересказываем эхом


def failed_runs(runs: Sequence[dict], jobs_of: Callable[[int], list],
                now: datetime, window_h: int = WINDOW_H) -> dict[str, int]:
    """{воркфлоу: число падений по расписанию за окно}, без отказов GitHub."""
    since = now - timedelta(hours=window_h)
    out: dict[str, int] = {}
    for r in runs:
        if r.get("conclusion") != "failure" or r.get("event") != "schedule":
            continue
        if r.get("name") == OWN_WORKFLOW:
            continue
        try:
            created = datetime.strptime(
                r["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        if not since <= created <= now:
            continue
        jobs = jobs_of(r["id"]) or []
        if not any(j.get("steps") for j in jobs):
            # Раннер не выдан: заданий нет вовсе (heartbeat 09.11) или они
            # без шагов (daily-monitor 05.10). Не наш отказ.
            continue
        out[r["name"]] = out.get(r["name"], 0) + 1
    return out


def render(failed: dict[str, int]) -> Optional[str]:
    if not failed:
        return None
    lines = ["⚙️ <b>Упали прогоны по расписанию</b>", ""]
    for name, n in sorted(failed.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"🔴 {name}" + (f" ×{n}" if n > 1 else ""))
    lines += ["", "<i>Данные этих прогонов не сохранены. Причина — в логах "
                  "GitHub Actions, вкладка Actions → фильтр «failure».</i>"]
    return "\n".join(lines)
