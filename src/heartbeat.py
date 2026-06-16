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


def _tactical_line() -> Optional[str]:
    """Сводка текущих тактических вердиктов (мягкий fail-safe)."""
    try:
        import json
        from src.tactical_signals import tactical_state_summary
        p = _REPO_ROOT / "state" / "tactical_state.json"
        state = json.loads(p.read_text()) if p.exists() else {}
        return tactical_state_summary(state, datetime.now(timezone.utc))
    except Exception as e:  # noqa: BLE001
        print(f"[heartbeat] tactical summary n/a: {e}")
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
