"""Чтение контракта лестницы цикла из OracAI (state/cycle_ladder.json).

Лестница — СТРАТЕГИЧЕСКИЙ слой: события «BUY 30% капитала» / «SELL 25% стэка»
по позиции в цикле (MVRV-зоны + SMA200-триггеры), для core-капитала на
горизонте цикла. Planner — ТАКТИЧЕСКИЙ: недельный DCA на перпах со стопами,
который правильно пропускает медвежьи недели.

Эти два слоя НАМЕРЕННО могут расходиться (лестница покупает страх, planner
ждёт подтверждённый тренд) — поэтому вердикты planner'а здесь не трогаются.
Модуль даёт контекстный блок в субботний отчёт, чтобы «SKIP недели» рядом с
«BUY 30%» читались как два пула капитала, а не как противоречие.

Fail-safe по спеке контракта: недоступен или date_utc старше 3 дней → None
(блок просто не показывается). Лестница информационна для planner'а — в
отличие от OracAI snapshot, который обязан падать громко.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any, Optional

import requests

_LADDER_URL = os.environ.get(
    "ORACAI_LADDER_URL",
    "https://raw.githubusercontent.com/BRKME/OracAI/main/state/cycle_ladder.json",
)
_MAX_AGE_DAYS = 3   # из спеки контракта (docs/CYCLE_LADDER_POLICY_v1.md)


def _http_get_json(url: str) -> dict[str, Any]:
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_ladder() -> Optional[dict[str, Any]]:
    """Контракт лестницы или None (мягкий fail-safe, без исключений)."""
    try:
        data = _http_get_json(_LADDER_URL)
        if not isinstance(data, dict):
            return None
        if "signal" not in data or "zone" not in data:
            return None
        d = datetime.strptime(str(data.get("date_utc", "")), "%Y-%m-%d").date()
        if (date.today() - d).days > _MAX_AGE_DAYS:
            return None
        return data
    except Exception:
        return None


def render_context(contract: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Готовит блок лестницы для шаблона отчёта.

    status_line — всегда (зона/MVRV/политика); event_line — только если за
    последний дневной прогон было событие BUY/SELL (редкие развороты цикла).
    """
    if not contract:
        return None
    zone = contract.get("zone", "?")
    mvrv = contract.get("mvrv")
    sig = contract.get("signal") or {}
    action = (sig.get("action") or "HOLD").upper()

    mvrv_part = f", MVRV {mvrv:.2f}" if isinstance(mvrv, (int, float)) else ""
    status_line = (f"Зона цикла: {zone}{mvrv_part} "
                   f"({sig.get('policy_version') or contract.get('policy_version', '')})")

    event_line = None
    if action == "BUY":
        event_line = (f"🟢 Событие лестницы: ПОКУПКА на "
                      f"{sig.get('fraction_of_capital', 0) * 100:.0f}% капитала "
                      f"— {sig.get('rationale', '')}")
    elif action == "SELL":
        event_line = (f"🔴 Событие лестницы: ФИКСАЦИЯ "
                      f"{sig.get('fraction_of_stack', 0) * 100:.0f}% стэка "
                      f"— {sig.get('rationale', '')}")

    return {
        "zone": zone,
        "status_line": status_line,
        "event_line": event_line,
        "note": ("Лестница — стратегический core-капитал на горизонт цикла; "
                 "недельный план ниже — тактический DCA. Они могут расходиться: "
                 "лестница покупает страх, план ждёт подтверждённый тренд."),
    }
