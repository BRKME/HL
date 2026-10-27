"""Теневая проверка шорта новых листингов HL вне выборки (27.10.2026).

Бэктест (`scripts/listing_fade_run.py`) прошёл правило, но интервалы не
отделили эффект от случайного шорта тех же монет. Решение оператора —
проверить вне выборки: каждый новый листинг записывается модельным шортом
по правилу, выбранному ДО этих данных: вход по закрытию первого полного
дня, стоп −15% по максимумам (при гэпе — по открытию), выход через 30 дней.

В канал не пишет: это замер, а не сигнал. Итог — по правилу в политике.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

from src.listing_fade import ENTRY_IDX, short_return

HORIZON_D = 30
STOP_PCT = 15.0
DAY_MS = 86_400_000

_REPO = Path(__file__).resolve().parent.parent
STATE_PATH = _REPO / "state" / "listing_shadow.json"


def _evaluate(candles: list, entry_t: int, horizon: int, stop: float
              ) -> Optional[tuple[float, bool]]:
    """Закрыта ли сделка: стоп внутри имеющихся свечей или истёк горизонт."""
    idx = next((i for i, c in enumerate(candles) if c["t"] == entry_t), None)
    if idx is None:
        return None
    avail = len(candles) - 1 - idx
    if avail <= 0:
        return None
    # Стоп проверяем на всём, что уже есть; срок — только когда наступил.
    r = short_return(candles, idx, min(avail, horizon), stop)
    if r is None:
        return None
    ret, stopped = r
    if stopped or avail >= horizon:
        return ret, stopped
    return None


def step(state: dict, meta: dict, candles_of: Callable[[str], list]
         ) -> tuple[dict, list[str]]:
    """Один проход: новые листинги → сделки, открытые → проверка выхода."""
    events: list[str] = []
    live = {c for c, m in meta.items() if not (m or {}).get("delisted")}
    if "seen" not in state:
        # Первый запуск: всё, что уже торгуется, — не листинг.
        return {"seen": sorted(meta), "trades": [], "pending": []}, events

    seen = set(state["seen"])
    trades = list(state.get("trades", []))
    pending = [c for c in state.get("pending", [])]
    for coin in sorted(live - seen):
        if coin not in pending:
            pending.append(coin)

    still: list[str] = []
    for coin in pending:
        try:
            cs = candles_of(coin) or []
        except Exception:  # noqa: BLE001
            still.append(coin)
            continue
        if len(cs) <= ENTRY_IDX:
            still.append(coin)
            continue
        entry = float(cs[ENTRY_IDX]["c"])
        trades.append({"coin": coin, "listing_t": cs[0]["t"],
                       "entry_t": cs[ENTRY_IDX]["t"], "entry": entry,
                       "status": "open"})
        events.append(f"открыт шорт {coin} по {entry:g}")

    for t in trades:
        if t["status"] != "open":
            continue
        try:
            cs = candles_of(t["coin"]) or []
        except Exception:  # noqa: BLE001
            continue
        res = _evaluate(cs, t["entry_t"], HORIZON_D, STOP_PCT)
        if res is None and (meta.get(t["coin"]) or {}).get("delisted") and cs:
            last = float(cs[-1]["c"])
            res = (-(last / t["entry"] - 1) * 100, False)
            t["reason"] = "delisted"
        if res is not None:
            t["ret"], t["stopped"] = round(res[0], 4), bool(res[1])
            t["status"] = "closed"
            events.append(f"закрыт {t['coin']}: {t['ret']:+.1f}%"
                          + (" (стоп)" if t["stopped"] else ""))

    return {"seen": sorted(seen | set(meta)), "trades": trades,
            "pending": still}, events


def main() -> int:
    from src.hl_api import fetch_candles, fetch_meta_and_ctxs

    state = (json.loads(STATE_PATH.read_text())
             if STATE_PATH.exists() else {})
    meta = fetch_meta_and_ctxs()
    lookback = HORIZON_D + 60   # запас на пропущенные прогоны

    def candles_of(coin: str) -> list:
        time.sleep(0.2)
        return fetch_candles(coin, interval="1d", lookback_days=lookback)

    state, events = step(state, meta, candles_of)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    closed = [t for t in state["trades"] if t["status"] == "closed"]
    for e in events:
        print(e)
    print(f"[listing-shadow] открытых "
          f"{sum(t['status'] == 'open' for t in state['trades'])}, "
          f"закрытых {len(closed)}, ждут первого дня {len(state['pending'])}")
    if closed:
        avg = sum(t["ret"] for t in closed) / len(closed)
        print(f"[listing-shadow] средняя закрытых {avg:+.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
