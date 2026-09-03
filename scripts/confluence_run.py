#!/usr/bin/env python3
"""Стечение признаков на BTC и ETH. Запускается вручную.

Проверяет то, чего мы не делали: объём как признак и совпадение
признаков. Каждый компонент по отдельности уже измерен и преимущества не
дал; источники говорят, что так и должно быть — подтверждает сигнал
только стечение.

КРИТЕРИЙ, ЗАПИСАННЫЙ ДО ПРОГОНА: группа «все три признака» должна
обгонять «все события кита» минимум на 1 п.п. средней доходности при
n>=30. Меньше тридцати — «судить нельзя», та же мерка, что применялась ко
всем прежним гипотезам. Двойного стандарта быть не должно.
"""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.confluence import COINS, annotate, evaluate, whale_events  # noqa: E402
from src.hl_api import fetch_candles  # noqa: E402

MIN_NOTIONAL = float(os.environ.get("CONF_MIN_NOTIONAL") or 25_000)
HORIZON_H = int(os.environ.get("CONF_HORIZON_H") or 24)
MIN_N = 30            # порог, ниже которого вывод не делается
EDGE_PP = 1.0         # требуемый обгон в процентных пунктах


def main() -> int:
    path = REPO / "state" / "whale_fills.jsonl"
    if not path.exists():
        print("нет state/whale_fills.jsonl")
        return 1
    fills = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]

    events = whale_events(fills, min_notional=MIN_NOTIONAL)
    print(f"# Стечение признаков · BTC+ETH · порог ${MIN_NOTIONAL:,.0f} · "
          f"горизонт {HORIZON_H}ч\n")
    print(f"событий кита: {len(events)}")
    if not events:
        return 1

    candles = {}
    for c in COINS:
        try:
            candles[c] = fetch_candles(c, interval="1h", lookback_days=120)
        except Exception as e:  # noqa: BLE001
            print(f"  {c}: свечи недоступны ({e})")
    if not candles:
        print("свечи не получены")
        return 1

    marked = annotate(events, candles)
    results = evaluate(marked, candles, horizon_h=HORIZON_H)

    print(f"\n{'группа':22} {'n':>5} {'средняя':>9} {'медиана':>9} {'WR':>6}")
    for o in results:
        if not o.n:
            print(f"{o.label:22} {0:>5}  нет данных")
            continue
        print(f"{o.label:22} {o.n:>5} {o.avg_pct:>+8.2f}% "
              f"{o.median_pct:>+8.2f}% {o.win_rate:>5.0%}")

    base = next((o for o in results if o.label == "все события кита"), None)
    best = next((o for o in results if o.label == "все три признака"), None)

    if not best or best.n < MIN_N:
        verdict = (f"СУДИТЬ НЕЛЬЗЯ: {best.n if best else 0} событий при "
                   f"минимуме {MIN_N}")
    elif base is None or base.avg_pct is None or best.avg_pct is None:
        verdict = "СУДИТЬ НЕЛЬЗЯ: базовая группа пуста"
    else:
        edge = best.avg_pct - base.avg_pct
        verdict = (f"стечение даёт {edge:+.2f} п.п. при пороге "
                   f"{EDGE_PP:+.1f} — "
                   + ("ПОДТВЕРЖДЕНО" if edge >= EDGE_PP else "не подтверждено"))
    print(f"\nВЫВОД: {verdict}")

    try:
        from src.telegram_sender import send_messages
        rows = "\n".join(
            f"{o.label:20} n={o.n:>4} {o.avg_pct:+.2f}% WR {o.win_rate:.0%}"
            for o in results if o.n)
        send_messages([
            f"🔗 <b>Стечение признаков</b> · BTC+ETH · "
            f"порог ${MIN_NOTIONAL:,.0f} · {HORIZON_H}ч\n"
            f"<pre>{rows}</pre>\n<b>{verdict}</b>\n"
            f"<i>критерий записан до прогона · без комиссий</i>"])
    except Exception as e:  # noqa: BLE001
        print(f"[confluence] отправка не удалась: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
