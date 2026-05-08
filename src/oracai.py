"""Чтение OracAI snapshot — последнее сохранённое состояние.

Берём через github.com/raw — public репо, без авторизации.
Если поле `cycle` отсутствует (старая версия OracAI) — фейлимся явно,
чтобы было видно в алерте, а не молча давали бы плохой совет.
"""
from __future__ import annotations

import json
import os
from typing import Any

import requests

_ORACAI_SNAPSHOT_URL = os.environ.get(
    "ORACAI_SNAPSHOT_URL",
    "https://raw.githubusercontent.com/BRKME/OracAI/main/state/last_output.json",
)


class OracAISnapshotError(RuntimeError):
    pass


def fetch_snapshot() -> dict[str, Any]:
    """Загружает и валидирует последний snapshot OracAI."""
    try:
        r = requests.get(_ORACAI_SNAPSHOT_URL, timeout=20)
        r.raise_for_status()
    except Exception as e:
        raise OracAISnapshotError(f"Не удалось загрузить OracAI snapshot: {e}") from e

    try:
        data = r.json()
    except Exception as e:
        raise OracAISnapshotError(f"OracAI snapshot не JSON: {e}") from e

    # Минимальный обязательный набор полей
    required_top = ("regime", "asset_allocation", "cycle", "risk", "confidence")
    missing = [k for k in required_top if k not in data]
    if missing:
        raise OracAISnapshotError(
            f"OracAI snapshot неполон, отсутствуют поля: {missing}. "
            "Нужна версия OracAI с export'ом cycle (commit 2d9dfe5+)."
        )

    return data


def derive_signal_strength(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Преобразует OracAI snapshot в сигнал: STRONG / MODERATE / SKIP / EXIT.

    Логика:
      EXIT      : risk_state в (CRISIS, TAIL) ИЛИ regime=BEAR ИЛИ action=SELL
      SKIP      : top_proximity ≥ 0.70 ИЛИ (risk=ELEVATED И conf<0.30)
                  ИЛИ action=FIX
      STRONG    : action в (BUY, ACCUMULATE) И risk=NORMAL И bottom%≥0.30
                  → leverage = 2x
      MODERATE  : action=HOLD И regime=BULL И conf>0.50 И risk≤ELEVATED
                  И top%<0.70
                  → leverage = 1x
      WEAK      : всё остальное → SKIP

    Возвращает:
      {
        "signal": "STRONG" | "MODERATE" | "SKIP" | "EXIT",
        "leverage": 0 | 1 | 2,
        "reasons": [строки причин],
        "raw": подмножество полей snapshot для отчёта
      }
    """
    cycle = snapshot["cycle"]
    risk = snapshot["risk"]
    regime = snapshot["regime"]
    conf = snapshot["confidence"].get("quality_adjusted", 0.0) or 0.0

    risk_state = (risk.get("risk_state") or "").upper()
    action = (cycle.get("action") or "").upper()
    top_pct = float(cycle.get("top_proximity") or 0.0)
    bot_pct = float(cycle.get("bottom_proximity") or 0.0)

    reasons: list[str] = []

    # 1. EXIT triggers
    if risk_state in ("CRISIS", "TAIL"):
        reasons.append(f"Риск = {risk_state}")
        return {"signal": "EXIT", "leverage": 0, "reasons": reasons,
                "raw": _raw_subset(snapshot)}
    if regime == "BEAR":
        reasons.append("Режим = BEAR")
        return {"signal": "EXIT", "leverage": 0, "reasons": reasons,
                "raw": _raw_subset(snapshot)}
    if action in ("SELL", "ПРОДАВАТЬ"):
        reasons.append(f"OracAI: {action}")
        return {"signal": "EXIT", "leverage": 0, "reasons": reasons,
                "raw": _raw_subset(snapshot)}

    # 2. SKIP triggers (близко к топу или высокий риск без уверенности)
    if top_pct >= 0.70:
        reasons.append(f"Top% = {top_pct:.0%} (порог skip = 70%)")
        return {"signal": "SKIP", "leverage": 0, "reasons": reasons,
                "raw": _raw_subset(snapshot)}
    if risk_state == "ELEVATED" and conf < 0.30:
        reasons.append(f"Риск=ELEVATED + низкая уверенность {conf:.0%}")
        return {"signal": "SKIP", "leverage": 0, "reasons": reasons,
                "raw": _raw_subset(snapshot)}
    if action in ("FIX", "ФИКСИРОВАТЬ"):
        reasons.append(f"OracAI: {action}")
        return {"signal": "SKIP", "leverage": 0, "reasons": reasons,
                "raw": _raw_subset(snapshot)}

    # 3. STRONG: явная покупка от OracAI + норма по риску
    buy_actions = ("BUY", "ПОКУПАТЬ", "ACCUMULATE", "ДОКУПИТЬ")
    if action in buy_actions and risk_state == "NORMAL" and bot_pct >= 0.30:
        reasons.append(f"OracAI: {action}")
        reasons.append(f"Риск = NORMAL")
        reasons.append(f"Bottom% = {bot_pct:.0%} (≥30%)")
        return {"signal": "STRONG", "leverage": 2, "reasons": reasons,
                "raw": _raw_subset(snapshot)}

    # 4. MODERATE: бычий регим без перегрева, OracAI говорит держать
    if (regime == "BULL"
            and risk_state in ("NORMAL", "ELEVATED")
            and conf >= 0.50
            and top_pct < 0.70):
        reasons.append(f"Режим = BULL, conf {conf:.0%}")
        reasons.append(f"Top% = {top_pct:.0%} (<70%)")
        reasons.append(f"Действие OracAI: {action}")
        return {"signal": "MODERATE", "leverage": 1, "reasons": reasons,
                "raw": _raw_subset(snapshot)}

    # 5. Default: SKIP
    reasons.append(
        f"Не выполнено условие STRONG/MODERATE: "
        f"режим={regime}, риск={risk_state}, conf={conf:.0%}, "
        f"top%={top_pct:.0%}, bot%={bot_pct:.0%}, action={action}"
    )
    return {"signal": "SKIP", "leverage": 0, "reasons": reasons,
            "raw": _raw_subset(snapshot)}


def _raw_subset(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Только то, что нужно для отчёта — без раздутого внутреннего state."""
    cycle = snapshot.get("cycle", {})
    risk = snapshot.get("risk", {})
    return {
        "regime": snapshot.get("regime"),
        "regime_probs": snapshot.get("probabilities"),
        "confidence": (snapshot.get("confidence") or {}).get("quality_adjusted"),
        "risk_state": risk.get("risk_state"),
        "phase": cycle.get("phase"),
        "cycle_position": cycle.get("cycle_position"),
        "bottom_proximity": cycle.get("bottom_proximity"),
        "top_proximity": cycle.get("top_proximity"),
        "action": cycle.get("action"),
        "rsi_d1_btc": cycle.get("rsi_d1"),
    }
