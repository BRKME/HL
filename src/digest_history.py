"""Новизна сигнала в ежедневной сводке.

Читая письмо, оператор не мог понять: `NEAR LONG` — это сегодняшний сигнал
или он висит третий день. От этого зависит поведение: новый сигнал — повод
действовать, тот же самый третий день — повод не действовать, его уже
видели и решение приняли. Без отметки приходилось держать вчерашнее письмо
в голове.

Хранится минимум: по каждой открытой идее её направление и дата появления.
Смена направления считается новой идеей, исчезновение — забывается.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = "digest_prev.json"
_ENTRY = ("LONG", "SHORT")
_COIN, _VERDICT = 0, 2


def load_prev(state_dir: Path) -> dict:
    try:
        raw = json.loads((Path(state_dir) / STATE_FILE).read_text())
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def save_prev(state_dir: Path, state: dict) -> None:
    p = Path(state_dir) / STATE_FILE
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    except OSError as e:
        logger.warning("Could not persist digest history: %s", e)


def _days_since(since: str, today: date) -> Optional[int]:
    try:
        return (today - date.fromisoformat(since)).days
    except (ValueError, TypeError):
        return None


def mark_novelty(verdicts, prev: dict, today: date) -> tuple[dict, dict]:
    """Вернуть (пометки по монетам, новое состояние).

    Пометка — «🆕» для впервые появившейся идеи и «N-й день» для той, что
    держится. Ожидание не помечается вовсе: помечать нечего, идеи нет.
    """
    marks: dict[str, str] = {}
    new_state: dict[str, dict] = {}

    for v in verdicts:
        coin, verdict = str(v[_COIN]), v[_VERDICT]
        if verdict not in _ENTRY:
            continue

        old = prev.get(coin)
        old_verdict = old.get("verdict") if isinstance(old, dict) else None
        since = old.get("since") if isinstance(old, dict) else None
        days = _days_since(since, today) if since else None

        if old_verdict != verdict or days is None:
            marks[coin] = "🆕"
            new_state[coin] = {"verdict": verdict, "since": today.isoformat()}
        else:
            marks[coin] = f"{days + 1}-й день"
            new_state[coin] = {"verdict": verdict, "since": since}

    return marks, new_state
