#!/usr/bin/env python3
"""Отчёт по гипотезе H1 к чекпойнту 23.08. Запускается вручную.

Пороги и решающие правила — в src/h1_metrics.py, зарегистрированы 08.08 до
сбора данных (docs/OPERATING_POLICY.md §3).
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.h1_metrics import compute_h1, verdict_h1  # noqa: E402


def _load(name: str) -> list[dict]:
    p = REPO / "state" / name
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def _regime_changes(days: int, now: datetime) -> int:
    """Сколько раз режим сменился за окно."""
    rows = sorted(_load("verdict_journal.jsonl"), key=lambda r: r.get("ts", ""))
    cutoff = (now - timedelta(days=days)).isoformat()
    seq, changes = None, 0
    for r in rows:
        if r.get("ts", "") < cutoff:
            continue
        reg = r.get("regime")
        if reg and reg != seq:
            if seq is not None:
                changes += 1
            seq = reg
    return changes


def main() -> int:
    now = datetime.now(timezone.utc)
    rows = _load("tactical_journal.jsonl")
    flips = _regime_changes(30, now)
    res = compute_h1(rows, now=now, regime_changes_30d=flips)

    print(f"# H1 — дребезг режима · {now.date()}\n")
    print(f"выходов на policy_version=2 : {res.n_exits}")
    print(f"доля regime_flip            : {res.regime_flip_share:.0%}")
    print(f"возвратов внутри 24 ч       : {res.reentry_rate:.0%}")
    med = res.regime_flip_median_r
    print(f"медиана R по regime_flip    : {med:+.3f}" if med is not None
          else "медиана R по regime_flip    : нет данных")
    print(f"смен режима за 30 дней      : {res.regime_flips_30d}")
    print(f"\nВЕРДИКТ: {verdict_h1(res)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
