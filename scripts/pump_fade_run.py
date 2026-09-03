#!/usr/bin/env python3
"""Шорт экстремально выросших монет. Запускается вручную.

Первая проверяемая идея, идущая против тренда. Считает не только среднюю
доходность, но и ХУДШУЮ сделку: у шорта убыток не ограничен, и средняя его
прячет. Плюс контроль — шорт в случайный момент, иначе нельзя отличить
«работает шорт после роста» от «монета просто падала».

Фандинг здесь работает ЗА нас: во время роста ставка положительна, и шорт
её получает. Весь месяц наши сигналы были лонгами, платившими 11% годовых.

КРИТЕРИЙ, ЗАПИСАННЫЙ ДО ПРОГОНА: событий не меньше 30, средняя доходность
шорта после роста должна обгонять случайный шорт минимум на 3 п.п., и
худшая сделка не должна превышать −40%. Последнее — про выживание: одна
сделка не должна уносить счёт.
"""
import os
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.hl_api import fetch_candles  # noqa: E402
from src.pump_fade import baseline, evaluate, find_pumps  # noqa: E402
from src.whitelist_focus import FOCUS_COINS  # noqa: E402

PUMP_PCT = float(os.environ.get("PUMP_PCT") or 50)
WINDOW_D = int(os.environ.get("PUMP_WINDOW_D") or 7)
HORIZONS = (3, 7, 14, 30)
MIN_EVENTS = 30
EDGE_PP = 3.0
WORST_LIMIT = -40.0


def main() -> int:
    print(f"# Шорт после роста · +{PUMP_PCT:g}% за {WINDOW_D} дн · "
          f"{len(FOCUS_COINS)} монет\n")

    per_h: dict[int, list] = {h: [] for h in HORIZONS}
    base_h: dict[int, list] = {h: [] for h in HORIZONS}
    total_events = 0

    for coin in FOCUS_COINS:
        try:
            candles = fetch_candles(coin, interval="1d", lookback_days=2000)
        except Exception as e:  # noqa: BLE001
            print(f"  {coin}: {e}")
            continue
        closes = [float(c["c"]) for c in candles if c.get("c")]
        if len(closes) < 120:
            continue
        events = find_pumps(closes, PUMP_PCT, WINDOW_D)
        total_events += len(events)
        print(f"  {coin:8} дней {len(closes):>5} · событий {len(events)}")
        for h in HORIZONS:
            r = evaluate(closes, events, h)
            if r.n:
                per_h[h].append(r)
            b = baseline(closes, h)
            if b.n:
                base_h[h].append(b)

    if not total_events:
        print("\nсобытий не найдено — снизить порог")
        return 1

    print(f"\nвсего событий: {total_events}\n")
    print(f"{'гориз':>6} {'n':>5} {'шорт':>8} {'медиана':>9} {'WR':>6} "
          f"{'худшая':>9} {'+фандинг':>10} {'случайный':>11} {'обгон':>8}")
    rows = []
    for h in HORIZONS:
        rs = per_h[h]
        bs = base_h[h]
        if not rs:
            continue
        n = sum(r.n for r in rs)
        avg = statistics.mean([r.avg_pct for r in rs])
        med = statistics.median([r.median_pct for r in rs])
        wr = statistics.mean([r.win_rate for r in rs])
        worst = min(r.worst_pct for r in rs)
        with_f = statistics.mean([r.avg_with_funding_pct for r in rs])
        b_avg = statistics.mean([b.avg_pct for b in bs]) if bs else 0.0
        edge = with_f - b_avg
        rows.append((h, n, avg, med, wr, worst, with_f, b_avg, edge))
        print(f"{h:>5}д {n:>5} {avg:>+7.2f}% {med:>+8.2f}% {wr:>5.0%} "
              f"{worst:>+8.1f}% {with_f:>+9.2f}% {b_avg:>+10.2f}% "
              f"{edge:>+7.2f}")

    best = max(rows, key=lambda r: r[8]) if rows else None
    if not best or best[1] < MIN_EVENTS:
        verdict = (f"СУДИТЬ НЕЛЬЗЯ: {best[1] if best else 0} событий при "
                   f"минимуме {MIN_EVENTS}")
    elif best[5] <= WORST_LIMIT:
        verdict = (f"НЕПРИЕМЛЕМЫЙ РИСК: худшая сделка {best[5]:+.0f}% "
                   f"при пределе {WORST_LIMIT:+.0f}%")
    elif best[8] >= EDGE_PP:
        verdict = (f"ПОДТВЕРЖДЕНО на {best[0]}д: обгон случайного шорта "
                   f"{best[8]:+.2f} п.п.")
    else:
        verdict = (f"не подтверждено: лучший обгон {best[8]:+.2f} п.п. "
                   f"при пороге {EDGE_PP:+.1f}")
    print(f"\nВЫВОД: {verdict}")

    try:
        from src.telegram_sender import send_messages
        table = "\n".join(
            f"{h:>2}д n={n:>4} шорт {with_f:+.2f}% vs случ {b_avg:+.2f}% "
            f"= {edge:+.2f}пп · худшая {worst:+.0f}%"
            for h, n, avg, med, wr, worst, with_f, b_avg, edge in rows)
        send_messages([
            f"📉 <b>Шорт после роста</b> · +{PUMP_PCT:g}% за {WINDOW_D}д\n"
            f"<pre>{table}</pre>\n<b>{verdict}</b>\n"
            f"<i>фандинг учтён В ПОЛЬЗУ шорта · худшая сделка показана: "
            f"убыток шорта не ограничен</i>"])
    except Exception as e:  # noqa: BLE001
        print(f"[pump] отправка не удалась: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
