"""Ежедневный heartbeat: «бот жив» — но ТОЛЬКО если за сутки в канал ничего
не отправлялось.

Зачем: после того как монитор замолчал без позиций (и субботний пост ушёл в
тишину), тишина в канале стала двусмысленной — «всё ок, нет позиций» или «бот
упал»? Heartbeat снимает двусмысленность, не возвращая шум: в дни с реальными
сообщениями (тактика/лестница/портфель) он молчит, потому что отправка уже
обновила маркер last-send. Появляется ровно в пустые дни.

Содержимое (выбор оператора): жив + текущий режим/фаза OracAI + есть ли
открытые позиции.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
LAST_SEND_PATH = _REPO_ROOT / "state" / "last_channel_send.txt"
SILENCE_HOURS = 24


def _read_last_send() -> Optional[str]:
    try:
        return LAST_SEND_PATH.read_text().strip() or None
    except Exception:
        return None


def should_send_heartbeat(last_send_ts: Optional[str], now: datetime) -> bool:
    """Слать heartbeat, если канал молчал >= SILENCE_HOURS (или ни разу не слал)."""
    if not last_send_ts:
        return True
    try:
        last = datetime.fromisoformat(last_send_ts)
    except ValueError:
        return True
    return (now - last) >= timedelta(hours=SILENCE_HOURS)


def build_heartbeat(regime: Optional[str], phase: Optional[str],
                    has_positions: bool, now: datetime,
                    tactical_line: Optional[str] = None) -> str:
    """Короткий статус: жив, режим/фаза, позиции, текущие тактические вердикты."""
    reg = regime or "n/a"
    ph = phase or "n/a"
    pos = "есть открытые позиции" if has_positions else "позиций нет (вне рынка)"
    date = now.strftime("%d.%m.%Y")
    lines = [f"✅ HL бот жив · {date}",
             f"Режим: {reg} · фаза: {ph}",
             f"Портфель: {pos}"]
    if tactical_line:
        lines.append(tactical_line)
    lines.append("<i>Тихий день — новых сигналов нет (вердикт не менялся). "
                 "Это статус-пинг, не сигнал.</i>")
    return "\n".join(lines)


def _latest_emitted(journal_path, coin: str) -> Optional[dict]:
    """Последний эмитированный сигнал по монете (entry/sl/direction)."""
    import json
    found = None
    try:
        for line in journal_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("coin") == coin and r.get("emitted"):
                found = r
    except Exception:
        return None
    return found


def _current_price(coin: str) -> Optional[float]:
    """Текущая цена монеты тем же путём, что тактика (последняя свеча HL).
    Мягкий fail-safe — без неё строка покажет вход/SL без «сейчас»."""
    try:
        from src import hl_api
        candles = hl_api.fetch_candles(coin, interval="1d", lookback_days=3)
        if candles:
            last = candles[-1]
            if isinstance(last, dict):
                return float(last.get("c") or last.get("close") or 0) or None
            return float(getattr(last, "close", 0)) or None
    except Exception:
        return None
    return None


def _tactical_line() -> Optional[str]:
    """Полный вид модели для heartbeat: направление, исходный вход @ цена, SL,
    текущая цена. Чтобы пропустивший алерт оператор видел уровни и мог войти
    по текущей цене, не дожидаясь следующего сигнала (~2 недели)."""
    try:
        import json
        from src.tactical_signals import tactical_levels_line
        sp = _REPO_ROOT / "state" / "tactical_state.json"
        jp = _REPO_ROOT / "state" / "tactical_journal.jsonl"
        state = json.loads(sp.read_text()) if sp.exists() else {}
        now = datetime.now(timezone.utc)
        signals = {}
        for coin, st in state.items():
            st = st or {}
            verdict = st.get("last_action_verdict") or st.get("last_verdict")
            if verdict not in ("LONG", "SHORT"):
                continue
            sig = _latest_emitted(jp, coin) or {}
            days = None
            ch = st.get("last_change_ts")
            if ch:
                try:
                    days = (now - datetime.fromisoformat(ch)).days
                except ValueError:
                    pass
            signals[coin] = {
                "direction": verdict,
                "entry": sig.get("entry"),
                "sl": sig.get("sl"),
                "tp": sig.get("tp"),
                "current": _current_price(coin),
                "days": days,
            }
        return tactical_levels_line(signals) or None
    except Exception as e:  # noqa: BLE001
        print(f"[heartbeat] tactical levels n/a: {e}")
        return None


def main() -> None:
    now = datetime.now(timezone.utc)
    if not should_send_heartbeat(_read_last_send(), now):
        print("[heartbeat] канал не молчал — пинг не нужен")
        return

    regime = phase = None
    has_positions = False
    try:
        from src import oracai
        snap = oracai.fetch_snapshot()
        regime = snap.get("regime")
        phase = (snap.get("cycle") or {}).get("phase")
    except Exception as e:  # noqa: BLE001
        print(f"[heartbeat] snapshot n/a: {e}")
    try:
        from src.daily_monitor import _build_portfolio, load_accounts, HLClient
        accounts = load_accounts(_REPO_ROOT / "whitelist.yaml")
        if accounts:
            pf = _build_portfolio(HLClient(), accounts)
            has_positions = (bool(pf.perp) and pf.total_account_value >= 5.0)
    except Exception as e:  # noqa: BLE001
        print(f"[heartbeat] portfolio n/a: {e}")

    msg = build_heartbeat(regime, phase, has_positions, now,
                          tactical_line=_tactical_line())
    print(msg)
    try:
        # Heartbeat — тоже сообщение в канал, поэтому шлём через send_messages:
        # он обновит last-send, и 24ч тишины отсчитаются заново. Это и не даёт
        # задвоить heartbeat при повторном прогоне в те же сутки.
        from src.telegram_sender import send_messages
        send_messages([msg])
    except Exception as e:  # noqa: BLE001
        print(f"[heartbeat] send failed: {e}")


if __name__ == "__main__":
    main()
