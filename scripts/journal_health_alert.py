#!/usr/bin/env python3
"""Run journal health checks and ping Telegram only when something is wrong.

Silence is the normal outcome. The point of this job is not a daily report —
it's that a dead data stream can no longer stay dead quietly.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.journal_health import run_checks, worst_severity  # noqa: E402
from src.whitelist_focus import FOCUS_COINS  # noqa: E402

JOURNAL = REPO_ROOT / "state" / "verdict_journal.jsonl"


def _load() -> list[dict]:
    import json
    if not JOURNAL.exists():
        return []
    out = []
    for line in JOURNAL.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def main() -> int:
    entries = _load()
    issues = run_checks(entries, datetime.now(timezone.utc), FOCUS_COINS)

    if not issues:
        print("[journal-health] всё пишется, тревожить не о чем")
        return 0

    lines = ["🩺 <b>Журнал: проблемы со сбором данных</b>", ""]
    for i in issues:
        mark = "🔴" if i.severity == "critical" else "⚠️"
        lines.append(f"{mark} {i.message}")
    lines.append("")
    lines.append("<i>Данные для разбора не копятся. Чем дольше это висит, "
                 "тем меньше выборка к чекпойнту.</i>")
    msg = "\n".join(lines)
    print(msg)

    try:
        from src.telegram_sender import send_messages
        send_messages([msg])
    except Exception as e:  # noqa: BLE001
        print(f"[journal-health] отправка не удалась: {e}", file=sys.stderr)
        return 1

    return 2 if worst_severity(issues) == "critical" else 0


if __name__ == "__main__":
    raise SystemExit(main())
