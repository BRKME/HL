"""Explicit «I'm holding this against the system» override.

A standing exit requirement repeats every two hours until the position is
closed or a fresh entry signal legitimises the hold. That's deliberate — it
was built so an exit couldn't drown in a flickering verdict. But it can't
tell two very different situations apart: a notification the operator
missed, and a decision the operator made.

Recording the second one explicitly is worth more than silencing it. The
requirement stays visible and named in the report, the line changes from
«ЗАКРОЙ» to «ДЕРЖУ ПРОТИВ СИСТЕМЫ» with a date and a reason, and the
override lands in the journal so that when we score exit policy the
overridden trades can be counted separately instead of polluting the
comparison as if the system had been followed.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = "manual_holds.json"


@dataclass(frozen=True)
class Hold:
    coin: str
    since: datetime
    note: str = ""


def _path(state_dir: Path) -> Path:
    return Path(state_dir) / STATE_FILE


def _read_raw(state_dir: Path) -> dict:
    try:
        raw = json.loads(_path(state_dir).read_text())
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def load_holds(state_dir: Path) -> dict[str, Hold]:
    out: dict[str, Hold] = {}
    for coin, rec in _read_raw(state_dir).items():
        if not isinstance(rec, dict):
            continue
        try:
            since = datetime.fromisoformat(rec.get("since", ""))
        except (ValueError, TypeError):
            continue
        out[coin] = Hold(coin=coin, since=since,
                         note=str(rec.get("note") or ""))
    return out


def _write(state_dir: Path, raw: dict) -> None:
    p = _path(state_dir)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(raw, ensure_ascii=False, indent=1))
    except OSError as e:
        logger.warning("Could not persist manual holds: %s", e)


def set_hold(coin: str, now: datetime, note: str, state_dir: Path) -> None:
    raw = _read_raw(state_dir)
    raw[coin.upper()] = {"since": now.isoformat(), "note": note or ""}
    _write(state_dir, raw)


def clear_hold(coin: str, state_dir: Path) -> None:
    raw = _read_raw(state_dir)
    if raw.pop(coin.upper(), None) is not None:
        _write(state_dir, raw)


def is_held(coin: str, state_dir: Path) -> bool:
    return coin.upper() in load_holds(state_dir)


def get_hold(coin: str, holds: Optional[dict[str, Hold]]) -> Optional[Hold]:
    return (holds or {}).get(coin.upper())
