#!/usr/bin/env python3
"""Затухание китового сигнала с задержкой. Запускается вручную.

Вопрос: живёт ли преимущество следования за китом достаточно долго, чтобы
за ним можно было успеть. Источники утверждают, что последователь отстаёт
на минуты и на неликвидных монетах это съедает бóльшую часть выигрыша.
Проверяем на своих данных: 120 тысяч заполнений 234 китов за 108 дней.

Цены берутся из ЧАСОВЫХ СВЕЧЕЙ, а не из заполнений китов. Заполнения
возникают, только когда кит торгует: по BTC ближайшая цена через 4 часа
после сделки находится в среднем через 129 минут, через сутки — через 578.
На таком индексе замер давал выборки по одному-три наблюдения.
"""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.hl_api import fetch_candles  # noqa: E402
from src.whale_delay import (  # noqa: E402
    DELAYS_MIN, HORIZONS_H, build_price_index_from_candles, decay_table,
)

TOP_COINS = int(os.environ.get("DELAY_TOP_COINS") or 8)


def main() -> int:
    path = REPO / "state" / "whale_fills.jsonl"
    if not path.exists():
        print("нет state/whale_fills.jsonl")
        return 1
    fills = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]

    opens = [f for f in fills
             if f.get("direction") in ("Open Long", "Open Short")
             and abs(float(f.get("notional_usd") or 0)) >= 100_000]
    import collections
    coins = [c for c, _ in collections.Counter(
        f["coin"] for f in opens if f.get("coin")).most_common(TOP_COINS)]
    print(f"# Затухание китового сигнала\n"
          f"заполнений {len(fills)}, крупных открытий {len(opens)}, "
          f"монет в разборе {len(coins)}\n")

    candles = {}
    for c in coins:
        try:
            candles[c] = fetch_candles(c, interval="1h", lookback_days=120)
        except Exception as e:  # noqa: BLE001
            print(f"  {c}: свечи недоступны ({e})")
    idx = build_price_index_from_candles(candles)
    if not idx:
        print("свечи не получены")
        return 1

    fills_in = [f for f in fills if f.get("coin") in idx]
    rows = [r for r in decay_table(fills_in, price_index=idx) if r.n >= 20]
    if not rows:
        print("выборок достаточного размера не набралось")
        return 1

    print(f"{'гориз':>6} {'задержка':>9} {'n':>5} {'средняя':>9} "
          f"{'медиана':>9} {'WR':>6}")
    prev = None
    for r in rows:
        if prev is not None and r.horizon_h != prev:
            print()
        prev = r.horizon_h
        d = f"{r.delay_min}м" if r.delay_min < 60 else f"{r.delay_min // 60}ч"
        print(f"{r.horizon_h:>5}ч {d:>9} {r.n:>5} {r.avg_pct:>+8.2f}% "
              f"{r.median_pct:>+8.2f}% {r.win_rate:>5.0%}")

    # Ключевой вопрос: сколько преимущества остаётся после задержки.
    by_h = {}
    for r in rows:
        by_h.setdefault(r.horizon_h, {})[r.delay_min] = r.avg_pct
    verdict_lines = []
    for h, d in sorted(by_h.items()):
        if 0 in d and d:
            fast = d[0]
            slow = {k: v for k, v in d.items() if k >= 60}
            if slow and fast:
                worst = min(slow.values())
                kept = worst / fast * 100 if fast else 0
                verdict_lines.append(
                    f"{h}ч: мгновенно {fast:+.2f}%, с задержкой от часа "
                    f"{worst:+.2f}% — остаётся {kept:.0f}%")
    for line in verdict_lines:
        print(f"\n{line}")

    try:
        from src.telegram_sender import send_messages
        table = "\n".join(
            f"{r.horizon_h:>2}ч задержка "
            f"{(str(r.delay_min) + 'м') if r.delay_min < 60 else str(r.delay_min // 60) + 'ч':>4}"
            f" n={r.n:>4} {r.avg_pct:+.2f}% WR {r.win_rate:.0%}"
            for r in rows)
        send_messages([
            f"🐋 <b>Затухание китового сигнала</b>\n"
            f"<pre>{table}</pre>\n"
            + ("\n".join(verdict_lines) or "")
            + "\n<i>цены из часовых свечей · без комиссий</i>"])
    except Exception as e:  # noqa: BLE001
        print(f"[delay] отправка не удалась: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
