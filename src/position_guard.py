"""Position guard — замкнутый контур надзора за отслеживаемыми позициями.

Пре-регистрация 03.07.2026 после кейса BTC/ETH шортов: ETH простоял за SL
3+ часа без алерта (heartbeat уровни ПОКАЗЫВАЛ, но никто не сторожил),
BTC держался против режимного флипа TRANSITION->BULL, случившегося утром.

Политика выхода (первое совпадение побеждает):
  1. sl_breach   — цена за стопом. Стоп — пре-коммит входа, не обсуждается.
  2. tp_hit      — цель достигнута.
  3. verdict_flip — вердикт монеты ушёл со стороны позиции (SHORT -> WAIT/LONG).
                    Условие входа умерло — позиция больше не системная.
  4. regime_flip — режим движка развернулся против стороны (SHORT в BULL,
                    LONG в BEAR), даже если вердикт ещё не пересчитан.

Гвард ТОЛЬКО сторожит, алертит и фиксирует выход в журнале/state —
входы и сайзинг не его зона. Отдельно: детект смены режима (разворот
тренда) алертится всегда, даже без позиций, с дедупом по guard_state.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = _REPO_ROOT / "state"
TACTICAL_STATE = STATE_DIR / "tactical_state.json"
TACTICAL_JOURNAL = STATE_DIR / "tactical_journal.jsonl"
GUARD_STATE = STATE_DIR / "guard_state.json"

_OPPOSES = {"SHORT": "BULL", "LONG": "BEAR"}


def evaluate_exit(sig: dict, price: Optional[float], verdict: Optional[str],
                  regime: Optional[str]) -> Optional[dict]:
    """Решение о выходе для одной позиции. None = держим (или нет данных).

    Чистая функция — вся политика здесь, тестируется без сети."""
    direction = sig.get("direction")
    entry, sl, tp = sig.get("entry"), sig.get("sl"), sig.get("tp")
    if direction not in ("LONG", "SHORT") or not all(
            isinstance(x, (int, float)) for x in (entry, sl, tp)):
        return None
    if price is None:
        return None  # fail-safe: вслепую не выходим, следующий тик проверит

    risk = abs(entry - sl)
    if risk <= 0:
        return None
    pnl_r = ((entry - price) if direction == "SHORT" else (price - entry)) / risk

    reason = None
    if direction == "SHORT" and price >= sl:
        reason = "sl_breach"
    elif direction == "LONG" and price <= sl:
        reason = "sl_breach"
    elif direction == "SHORT" and price <= tp:
        reason = "tp_hit"
    elif direction == "LONG" and price >= tp:
        reason = "tp_hit"
    elif verdict is not None and verdict != direction:
        reason = "verdict_flip"
    elif regime is not None and regime == _OPPOSES[direction]:
        reason = "regime_flip"

    if reason is None:
        return None
    return {"reason": reason, "exit_price": float(price),
            "pnl_r": round(pnl_r, 4), "direction": direction,
            "entry": entry, "sl": sl, "tp": tp}


def regime_changed(guard_state: dict, regime: Optional[str]) -> bool:
    """True один раз на каждую смену режима (дедуп через guard_state)."""
    if not regime:
        return False
    prev = guard_state.get("last_regime")
    guard_state["last_regime"] = regime
    return prev is not None and prev != regime or prev is None


_REASON_HUMAN = {
    "sl_breach": "\u274c ПРОБОЙ СТОПА",
    "tp_hit": "\u2705 ЦЕЛЬ ДОСТИГНУТА",
    "verdict_flip": "\u26a0\ufe0f ВЕРДИКТ УШЁЛ СО СТОРОНЫ ПОЗИЦИИ",
    "regime_flip": "\u26a0\ufe0f РЕЖИМ РАЗВЕРНУЛСЯ ПРОТИВ ПОЗИЦИИ",
}


def format_exit_alert(coin: str, ex: dict) -> str:
    return (f"{_REASON_HUMAN[ex['reason']]}\n"
            f"<b>{coin} {ex['direction']}</b> · вход ${ex['entry']:,.0f} · "
            f"сейчас ${ex['exit_price']:,.0f} · {ex['pnl_r']:+.2f}R\n"
            f"<i>Политика выхода ({ex['reason']}): закрыть позицию. "
            f"Это исполнение правила, зафиксированного на входе, не сигнал.</i>")


def format_regime_alert(prev: Optional[str], regime: str) -> str:
    return (f"\U0001f504 <b>РАЗВОРОТ РЕЖИМА: {prev or '—'} \u2192 {regime}</b>\n"
            f"<i>Верхний слой иерархии сменился — все открытые и планируемые "
            f"позиции переоцениваются относительно нового режима.</i>")


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _latest_emitted(coin: str) -> Optional[dict]:
    found = None
    try:
        for line in TACTICAL_JOURNAL.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("coin") == coin and r.get("emitted") and \
                    r.get("direction") in ("LONG", "SHORT"):
                found = r
    except Exception:
        return None
    return found


def run() -> int:
    """Один проход гварда. Возвращает число отправленных алертов."""
    from src.heartbeat import _current_price
    from src.telegram_sender import send_messages
    from src.oracai import fetch_snapshot

    tactical = _load(TACTICAL_STATE, {})
    guard = _load(GUARD_STATE, {})
    now = datetime.now(timezone.utc).isoformat()
    alerts: list[str] = []

    regime = None
    try:
        regime = (fetch_snapshot() or {}).get("regime")
    except Exception as e:  # noqa: BLE001
        print(f"[guard] regime n/a: {e}")

    prev_regime = guard.get("last_regime")
    if regime_changed(guard, regime) and prev_regime is not None:
        alerts.append(format_regime_alert(prev_regime, regime))

    for coin, st in (tactical or {}).items():
        st = st or {}
        direction = st.get("last_action_verdict")
        if direction not in ("LONG", "SHORT"):
            continue
        sig = _latest_emitted(coin)
        if not sig or sig.get("direction") != direction:
            continue
        price = _current_price(coin)
        verdict = st.get("last_verdict")
        ex = evaluate_exit(sig, price, verdict, regime)
        if not ex:
            continue
        alerts.append(format_exit_alert(coin, ex))
        # фиксация выхода: журнал + state (позиция закрыта в трекинге)
        rec = {"ts": now, "coin": coin, "direction": "EXIT",
               "exit_reason": ex["reason"], "exit_price": ex["exit_price"],
               "pnl_r": ex["pnl_r"], "entry": ex["entry"], "sl": ex["sl"],
               "tp": ex["tp"], "closed_direction": direction,
               "regime": regime, "emitted": True, "suppressed_by": None}
        with TACTICAL_JOURNAL.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        st["last_action_verdict"] = None
        st["last_change_ts"] = now
        tactical[coin] = st

    TACTICAL_STATE.write_text(json.dumps(tactical, ensure_ascii=False, indent=1))
    GUARD_STATE.write_text(json.dumps(guard, ensure_ascii=False, indent=1))

    if alerts:
        try:
            send_messages(alerts)
        except Exception as e:  # noqa: BLE001
            print(f"[guard] send failed: {e}")
    print(f"[guard] alerts={len(alerts)} regime={regime}")
    return len(alerts)


if __name__ == "__main__":
    run()
