#!/usr/bin/env python3
"""Сообщить в канал об упавших прогонах по расписанию. Молчит, если их нет.

Запускается из journal-health раз в сутки. Нужны GITHUB_TOKEN (права
actions: read) и GITHUB_REPOSITORY — в Actions заданы автоматически.
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.actions_health import failed_runs, render  # noqa: E402


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        print("[actions-health] нет GITHUB_REPOSITORY/GITHUB_TOKEN — пропуск")
        return 0
    api = f"https://api.github.com/repos/{repo}/actions"
    hdr = {"Authorization": f"Bearer {token}",
           "Accept": "application/vnd.github+json"}

    def _get(url, **params):
        r = requests.get(url, headers=hdr, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    runs = _get(f"{api}/runs", status="failure", per_page=50)["workflow_runs"]
    failed = failed_runs(
        runs, lambda rid: _get(f"{api}/runs/{rid}/jobs")["jobs"],
        datetime.now(timezone.utc))
    msg = render(failed)
    if not msg:
        print("[actions-health] упавших прогонов по расписанию нет")
        return 0
    print(msg)
    try:
        from src.telegram_sender import send_messages
        send_messages([msg])
    except Exception as e:  # noqa: BLE001
        print(f"[actions-health] отправка не удалась: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
