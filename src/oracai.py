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
    if data.get("cycle") is None:
        raise OracAISnapshotError(
            "OracAI snapshot пришёл с cycle=null. "
            "Это значит cycle_metrics_collector упал — проверь логи OracAI."
        )

    return data


def derive_signal_strength(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Преобразует OracAI snapshot в сигнал: STRONG / MODERATE / SKIP / EXIT.

    Логика учитывает что у OracAI два независимых движка:
      - regime detector → BULL/BEAR/RANGE/TRANS + risk_state (RISK_ON/RISK_OFF
        или редко NORMAL/ELEVATED/CRISIS/TAIL)
      - cycle engine → phase (EARLY_BEAR/MID_BULL/etc) + action (BUY/SELL/HOLD/etc)

    Они могут конфликтовать. Когда конфликтуют — НЕ доверяем ни одному в отдельности,
    идём в SKIP. EXIT — только когда сигналы согласованы.

    Сигнал согласован = (regime в BEAR/TRANS) И (cycle.action в SELL/STRONG_SELL)
                      ИЛИ risk_state в (CRISIS, TAIL).
    """
    cycle = snapshot.get("cycle") or {}
    risk = snapshot.get("risk") or {}
    regime = (snapshot.get("regime") or "").upper()
    conf = (snapshot.get("confidence") or {}).get("quality_adjusted", 0.0) or 0.0

    risk_state = (risk.get("risk_state") or "").upper()
    action = (cycle.get("action") or "").upper()
    phase = (cycle.get("phase") or "").upper()
    top_pct = float(cycle.get("top_proximity") or 0.0)
    bot_pct = float(cycle.get("bottom_proximity") or 0.0)

    bear_actions = ("SELL", "STRONG_SELL", "ПРОДАВАТЬ")
    buy_actions = ("BUY", "STRONG_BUY", "ACCUMULATE", "ПОКУПАТЬ", "ДОКУПИТЬ")
    bear_phases = ("EARLY_BEAR", "MID_BEAR", "LATE_BEAR", "DISTRIBUTION")
    bull_phases = ("EARLY_BULL", "MID_BULL", "LATE_BULL", "ACCUMULATION", "MARKUP")
    bullish_regimes = ("BULL",)
    bearish_regimes = ("BEAR", "TRANS")  # TRANS чаще ведёт вниз чем вверх
    bearish_risk = ("RISK_OFF", "CRISIS", "TAIL")
    elevated_risk = ("ELEVATED",)
    bullish_risk = ("RISK_ON", "NORMAL")

    reasons: list[str] = []

    # Конфликт между regime и cycle.phase — отмечаем для отчёта
    conflict = (
        (regime in bullish_regimes and phase in bear_phases) or
        (regime in bearish_regimes and phase in bull_phases)
    )

    # === 1. EXIT — только при согласованных bear-сигналах ===
    if risk_state in ("CRISIS", "TAIL"):
        reasons.append(f"Риск = {risk_state} (системный)")
        return _build("EXIT", 0, reasons, snapshot, conflict)

    if regime in bearish_regimes and action in bear_actions:
        reasons.append(f"Согласованный bear: режим={regime}, действие={action}")
        return _build("EXIT", 0, reasons, snapshot, conflict)

    if regime in bearish_regimes and risk_state == "RISK_OFF":
        reasons.append(f"Режим={regime} + RISK_OFF")
        return _build("EXIT", 0, reasons, snapshot, conflict)

    # === 2. SKIP — конфликт сигналов ===
    if conflict:
        reasons.append(
            f"Конфликт сигналов: режим={regime}, фаза={phase} → ждём разрешения"
        )
        return _build("SKIP", 0, reasons, snapshot, conflict)

    # === 3. SKIP — близко к топу или низкая уверенность у топа ===
    if top_pct >= 0.70:
        reasons.append(f"Top% = {top_pct:.0%} (порог skip = 70%)")
        return _build("SKIP", 0, reasons, snapshot, conflict)
    if risk_state in elevated_risk and conf < 0.30:
        reasons.append(f"Риск=ELEVATED + низкая уверенность {conf:.0%}")
        return _build("SKIP", 0, reasons, snapshot, conflict)
    if action in ("FIX", "ФИКСИРОВАТЬ"):
        reasons.append(f"OracAI: {action}")
        return _build("SKIP", 0, reasons, snapshot, conflict)

    # === 4. STRONG — согласованный bull + явная покупка ===
    if (regime in bullish_regimes
            and action in buy_actions
            and risk_state in bullish_risk
            and bot_pct >= 0.30):
        reasons.append(f"Согласованный bull: режим={regime}, действие={action}")
        reasons.append(f"Риск = {risk_state}")
        reasons.append(f"Bottom% = {bot_pct:.0%} (≥30%)")
        return _build("STRONG", 2, reasons, snapshot, conflict)

    # === 5. MODERATE — bull регим без покупки, но и без перегрева ===
    if (regime in bullish_regimes
            and risk_state in bullish_risk + elevated_risk
            and conf >= 0.50
            and top_pct < 0.70):
        reasons.append(f"Режим = {regime}, риск = {risk_state}, conf {conf:.0%}")
        reasons.append(f"Top% = {top_pct:.0%} (<70%)")
        if action:
            reasons.append(f"Действие OracAI: {action}")
        return _build("MODERATE", 1, reasons, snapshot, conflict)

    # === 6. Default fallback — SKIP ===
    reasons.append(
        f"Не выполнено условие STRONG/MODERATE: "
        f"режим={regime}, риск={risk_state}, conf={conf:.0%}, "
        f"top%={top_pct:.0%}, bot%={bot_pct:.0%}, фаза={phase}, action={action}"
    )
    return _build("SKIP", 0, reasons, snapshot, conflict)


def _build(signal: str, leverage: int, reasons: list[str],
           snapshot: dict, conflict: bool) -> dict[str, Any]:
    raw = _raw_subset(snapshot)
    raw["conflict"] = conflict
    return {
        "signal": signal,
        "leverage": leverage,
        "reasons": reasons,
        "raw": raw,
    }


def _raw_subset(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Только то, что нужно для отчёта — без раздутого внутреннего state."""
    cycle = snapshot.get("cycle") or {}
    risk = snapshot.get("risk") or {}
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
