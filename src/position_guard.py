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


def format_exit_alert(coin: str, ex: dict, real_side=None) -> str:
    """real_side: сторона РЕАЛЬНОЙ позиции в портфеле ('LONG'/'SHORT'),
    'FLAT' если портфель проверен и позиции нет, None если проверить не
    удалось. Кейс 04.07: гвард кричал «закрыть позицию» про модельные
    сигналы — оператор не мог отличить трекинг от реальности."""
    head = (f"{_REASON_HUMAN[ex['reason']]}\n"
            f"<b>{coin} {ex['direction']}</b> · вход ${ex['entry']:,.0f} · "
            f"сейчас ${ex['exit_price']:,.0f} · {ex['pnl_r']:+.2f}R\n")
    if real_side == ex["direction"]:
        tail = (f"<i>Позиция ЕСТЬ в портфеле — закрой её. Политика выхода "
                f"({ex['reason']}): исполнение правила, зафиксированного на входе.</i>")
    elif real_side is None:
        tail = (f"<i>Портфель проверить не удалось — проверь портфель сам: "
                f"если позиция открыта, закрой ({ex['reason']}). "
                f"В трекинге сигнал закрыт.</i>")
    else:
        tail = (f"<i>Модельный выход ({ex['reason']}): в портфеле позиции нет, "
                f"сигнал закрыт в трекинге для статистики. Действий не требуется.</i>")
    return head + tail


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


def _real_position_side(coin: str):
    """Сторона реальной perp-позиции по монете: 'LONG'/'SHORT', 'FLAT' если
    портфель прочитан и позиции нет, None при любом сбое (сеть/конфиг)."""
    try:
        from src.daily_monitor import load_accounts, _build_portfolio
        from src.hl_client import HLClient
        accounts = load_accounts(_REPO_ROOT / "whitelist.yaml")
        if not accounts:
            return None
        pf = _build_portfolio(HLClient(), accounts)
        for p in pf.perp:
            if p.coin == coin and abs(p.net_size) > 0:
                return "LONG" if p.net_size > 0 else "SHORT"
        return "FLAT"
    except Exception as e:  # noqa: BLE001
        print(f"[guard] portfolio n/a: {e}")
        return None


def _exit_already_recorded(coin: str, entry) -> bool:
    """EXIT по этой позиции (coin+entry) уже в журнале — не алертить дважды."""
    try:
        for line in TACTICAL_JOURNAL.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if (r.get("coin") == coin and r.get("direction") == "EXIT"
                    and r.get("entry") == entry):
                return True
    except Exception:
        return False
    return False


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
        if _exit_already_recorded(coin, sig.get("entry")):
            # выход уже зафиксирован в журнале, но state не очищен (частичный
            # сбой персистенции) — чиним state молча, без повторного алерта
            st["last_action_verdict"] = None
            st["last_change_ts"] = now
            tactical[coin] = st
            continue
        price = _current_price(coin)
        verdict = st.get("last_verdict")
        ex = evaluate_exit(sig, price, verdict, regime)
        if not ex:
            continue
        alerts.append(format_exit_alert(coin, ex, _real_position_side(coin)))
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

