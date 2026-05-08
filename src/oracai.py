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
    """Преобразует OracAI snapshot в недельный сигнал.

    КЛЮЧЕВОЙ КОНТЕКСТ: запуск раз в неделю. Пропустить вход = пропустить
    неделю DCA (необратимо). Пропустить выход = ничего, SL сработает сам
    в любой момент. Поэтому логика смещена в сторону входа.

    Приоритет: regime > cycle (regime ловит 20-дневное окно — как раз
    наш горизонт; cycle лагирует на разворотах и шумит на конфликтах).

    EXIT     : согласованный bear (regime BEAR/TRANS + risk RISK_OFF)
               ИЛИ системный риск (CRISIS/TAIL)
    SKIP     : top% ≥ 0.70 (явный перегрев)
               ИЛИ regime BEAR/TRANS (даже если cycle bull — не лезем)
    DEFENSIVE: regime BULL, но cycle bear (конфликт) → 1× только в BTC
               (через signal=MODERATE с пометкой defensive в reasons)
    MODERATE : regime BULL без конфликта, обычный bull без явной покупки
    STRONG   : regime BULL + cycle bull + явная покупка от OracAI
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
    bearish_regimes = ("BEAR", "TRANS")
    bullish_risk = ("RISK_ON", "NORMAL")

    reasons: list[str] = []
    defensive = False

    conflict = (
        (regime in bullish_regimes and phase in bear_phases) or
        (regime in bearish_regimes and phase in bull_phases)
    )

    # === 1. EXIT — системный риск или согласованный bear ===
    if risk_state in ("CRISIS", "TAIL"):
        reasons.append(f"Системный риск: {risk_state}")
        return _build("EXIT", 0, reasons, snapshot, conflict, defensive)

    if regime in bearish_regimes and (
        risk_state == "RISK_OFF" or action in bear_actions
    ):
        reasons.append(f"Рынок развернулся вниз — выходим в стейбл")
        return _build("EXIT", 0, reasons, snapshot, conflict, defensive)

    # === 2. SKIP — regime сам по себе bearish ===
    if regime in bearish_regimes:
        reasons.append(f"Рынок не в бычьей фазе — пропускаем неделю")
        return _build("SKIP", 0, reasons, snapshot, conflict, defensive)

    # === 3. SKIP — близко к топу ===
    if top_pct >= 0.70:
        reasons.append(f"Цена близко к локальному максимуму ({top_pct:.0%})")
        reasons.append("Покупать здесь — плохой риск/доходность")
        return _build("SKIP", 0, reasons, snapshot, conflict, defensive)

    # === 4. SKIP — низкая уверенность ===
    if conf < 0.30:
        reasons.append(f"OracAI не уверен в сигнале ({conf:.0%}) — ждём")
        return _build("SKIP", 0, reasons, snapshot, conflict, defensive)

    # === 5. DEFENSIVE — конфликт сигналов ===
    if regime in bullish_regimes and conflict:
        defensive = True
        reasons.append("Сигналы расходятся: рынок бычий, но цикл показывает разворот")
        reasons.append("Заходим осторожно — только BTC, без плеча")
        return _build("MODERATE", 1, reasons, snapshot, conflict, defensive)

    # === 6. STRONG — согласованный bull + явная покупка ===
    if (regime in bullish_regimes
            and action in buy_actions
            and risk_state in bullish_risk
            and bot_pct >= 0.30
            and not conflict):
        reasons.append("Полный bullish — рынок и цикл согласны")
        reasons.append(f"Цена далеко от пика ({top_pct:.0%}), есть пространство роста")
        return _build("STRONG", 2, reasons, snapshot, conflict, defensive)

    # === 7. MODERATE — обычный bull без явной покупки ===
    if (regime in bullish_regimes
            and risk_state in bullish_risk + ("ELEVATED",)
            and top_pct < 0.70):
        reasons.append("Рынок в бычьей фазе, но без сильного сигнала на докупку")
        return _build("MODERATE", 1, reasons, snapshot, conflict, defensive)

    # === 8. Default fallback ===
    reasons.append("Не выполнены условия для покупки — пропускаем")
    return _build("SKIP", 0, reasons, snapshot, conflict, defensive)


def _build(signal: str, leverage: int, reasons: list[str],
           snapshot: dict, conflict: bool, defensive: bool) -> dict[str, Any]:
    raw = _raw_subset(snapshot)
    raw["conflict"] = conflict
    raw["defensive"] = defensive
    return {
        "signal": signal,
        "leverage": leverage,
        "reasons": reasons,
        "raw": raw,
        "defensive": defensive,
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
