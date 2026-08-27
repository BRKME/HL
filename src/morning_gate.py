"""First-run-of-day gate for the morning whitelist digest.

Why this module exists (03.08.2026 post-mortem):

The standalone whitelist-focus workflow used to run on its own cron
("5 6 * * *") and wrote the daily verdicts — including verdict_raw and
rs_30d/rs_90d — into state/verdict_journal.jsonl. On 06.07 its cron was
disabled and the digest was folded into daily-monitor to avoid a second
Telegram ping. The folded-in branch was gated on `now.hour == 7`.

GitHub Actions does not deliver scheduled runs on time. Measured over two
weeks, the 07:00 UTC tick of daily-monitor landed at 08–10 UTC, never
inside hour 7. So the branch never ran, and four weeks of raw-verdict and
relative-strength observations were lost without a single error in the log.

The lesson is that a wall-clock equality is not a schedule. What we
actually mean is «the first run of the day, once it's late enough to be
morning» — which is what this gate implements, with the day marked done in
state so later ticks stay silent.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_FILE = "morning_digest.json"

# Нижняя граница «утра». Сводка задумывалась на 10:00 МСК, но Actions
# доставляет cron с задержкой 1–3 часа: при старте в 07:00 UTC первый тик
# дня падал в 09–10 UTC и сводка приходила в 12–13 МСК. Cron сдвинут на
# 05:00 UTC, граница — на 06:00 UTC (09:00 МСК), чтобы ранняя доставка не
# пролетала мимо окна. Прогоны до этого часа (ручной запуск, бэкфилл) день
# по-прежнему не сжигают.
EARLIEST_UTC_HOUR = 6

# Позже этого часа дайджест за день уже не отправляется. 03.08 гейт уехал в
# прод днём, первым же тиком после деплоя оказался 20:26 UTC — и утренняя
# сводка пришла в 23:26 МСК. Пропущенный день лучше ночного дайджеста:
# сводка «что покупать сегодня» в полночь бессмысленна, а привычка к
# ночным пингам стоит дороже одной пропущенной записи в журнал.
LATEST_UTC_HOUR = 14


def _path(state_dir: Path, name: str = STATE_FILE) -> Path:
    return Path(state_dir) / name


def _last_date(state_dir: Path, name: str = STATE_FILE) -> str | None:
    try:
        raw = json.loads(_path(state_dir, name).read_text())
        value = raw.get("last_date")
        return value if isinstance(value, str) else None
    except (OSError, ValueError, AttributeError):
        # Missing or corrupt state must not block the digest — running it
        # twice is a cosmetic annoyance, never running it cost us a month.
        return None


def in_digest_window(now: datetime) -> bool:
    """Пора ли БЕСПОКОИТЬ оператора сводкой.

    Отделено от «пора ли собирать данные» (27.08): окно нужно ради
    оператора, журналу оно ни к чему, а привязка сбора к окну стоила
    целого дня наблюдений.
    """
    return EARLIEST_UTC_HOUR <= now.hour <= LATEST_UTC_HOUR


def ran_today(now: datetime, state_dir: Path,
              name: str = STATE_FILE) -> bool:
    """Отработал ли уже сегодня — независимо от часа."""
    return _last_date(state_dir, name) == now.date().isoformat()


def should_run_digest(now: datetime, state_dir: Path,
                      name: str = STATE_FILE) -> bool:
    """True на первом тике дня внутри окна 07:00–14:00 UTC."""
    if not (EARLIEST_UTC_HOUR <= now.hour <= LATEST_UTC_HOUR):
        return False
    return _last_date(state_dir, name) != now.date().isoformat()


def mark_digest_done(now: datetime, state_dir: Path,
                     name: str = STATE_FILE) -> None:
    """Record that today's digest has been produced."""
    p = _path(state_dir, name)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"last_date": now.date().isoformat()},
                                ensure_ascii=False, indent=1))
    except OSError as e:
        logger.warning("Could not persist morning digest marker: %s", e)
