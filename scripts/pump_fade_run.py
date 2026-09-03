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
HORIZONS = (7, 14, 30)
# Сетка стопов с шагом. Верхняя граница — 25%: при плече 5x ликвидация
# наступает около 20%, и стоп шире неё бессмыслен, биржа закроет раньше.
STOPS = (None, 5, 8, 10, 12, 15, 18, 20, 25)
MIN_EVENTS = 30
EDGE_PP = 3.0
WORST_LIMIT = -40.0


def main() -> int:
    print(f"# Шорт после роста · +{PUMP_PCT:g}% за {WINDOW_D} дн · "
          f"{len(FOCUS_COINS)} монет\n")

    # Для каждой пары (горизонт, стоп) собираем результат по всем монетам.
    grid_rows: dict[tuple, list] = {}
    base_h: dict[int, list] = {h: [] for h in HORIZONS}
    total_events = 0

    for coin in FOCUS_COINS:
        try:
            candles = fetch_candles(coin, interval="1d", lookback_days=2000)
        except Exception as e:  # noqa: BLE001
            print(f"  {coin}: {e}")
            continue
        closes = [float(c["c"]) for c in candles if c.get("c")]
        highs = [float(c.get("h") or c["c"]) for c in candles if c.get("c")]
        if len(closes) < 120:
            continue
        events = find_pumps(closes, PUMP_PCT, WINDOW_D)
        total_events += len(events)
        print(f"  {coin:8} дней {len(closes):>5} · событий {len(events)}")
        for h in HORIZONS:
            b = baseline(closes, h)
            if b.n:
                base_h[h].append(b)
            for stop in STOPS:
                r = evaluate(closes, events, h, stop_pct=stop, highs=highs)
                if r.n:
                    grid_rows.setdefault((h, stop), []).append(r)

    if not total_events:
        print("\nсобытий не найдено — снизить порог")
        return 1

    print(f"\nвсего событий: {total_events}\n")
    print(f"{'гориз':>6} {'стоп':>6} {'n':>5} {'средняя':>9} {'медиана':>9} "
          f"{'худшая':>8} {'выбито':>7} {'случ':>8} {'обгон':>8}")
    rows = []
    for h in HORIZONS:
        bs = base_h[h]
        b_avg = statistics.mean([b.avg_pct for b in bs]) if bs else 0.0
        for stop in STOPS:
            rs = grid_rows.get((h, stop))
            if not rs:
                continue
            n = sum(r.n for r in rs)
            with_f = statistics.mean([r.avg_with_funding_pct for r in rs])
            med = statistics.median([r.median_pct for r in rs])
            worst = min(r.worst_pct for r in rs)
            stopped = statistics.mean(
                [r.stopped_share for r in rs if r.stopped_share is not None]
                or [0.0])
            edge = with_f - b_avg
            rows.append((h, stop, n, with_f, med, worst, stopped, b_avg, edge))
            lab = "нет" if stop is None else f"-{stop}%"
            print(f"{h:>5}д {lab:>6} {n:>5} {with_f:>+8.2f}% {med:>+8.2f}% "
                  f"{worst:>+7.1f}% {stopped:>6.0%} {b_avg:>+7.2f}% "
                  f"{edge:>+7.2f}")
        print()

    # Отбираем только то, что ПРОХОДИТ предел риска: преимущество при
    # неограниченном убытке нам не годится — при плече 5x счёт кончается
    # раньше, чем стратегия успеет отработать.
    survivable = [r for r in rows if r[5] > WORST_LIMIT and r[2] >= MIN_EVENTS]
    rows_for_best = survivable or rows

    best = max(rows_for_best, key=lambda r: r[8]) if rows_for_best else None
    if not best or best[2] < MIN_EVENTS:
        verdict = (f"СУДИТЬ НЕЛЬЗЯ: {best[2] if best else 0} событий при "
                   f"минимуме {MIN_EVENTS}")
    elif not survivable:
        verdict = (f"НЕПРИЕМЛЕМЫЙ РИСК при любом стопе: лучшая худшая "
                   f"{max(r[5] for r in rows):+.0f}%")
    elif best[8] >= EDGE_PP:
        verdict = (f"ПОДТВЕРЖДЕНО: {best[0]}д, стоп "
                   f"{'нет' if best[1] is None else str(-best[1]) + '%'}, "
                   f"обгон {best[8]:+.2f} п.п., худшая {best[5]:+.0f}%, "
                   f"выбито {best[6]:.0%}")
    else:
        verdict = (f"не подтверждено: лучший обгон {best[8]:+.2f} п.п. "
                   f"при пороге {EDGE_PP:+.1f}")
    print(f"\nВЫВОД: {verdict}")

    try:
        from src.telegram_sender import send_messages
        shown = sorted(rows_for_best, key=lambda r: -r[8])[:10]
        table = "\n".join(
            f"{h:>2}д стоп {'нет' if stop is None else '-' + str(stop) + '%':>5} "
            f"n={n:>4} {with_f:+.1f}% vs {b_avg:+.1f}% = {edge:+.1f}пп · "
            f"худш {worst:+.0f}% · выбито {stopped:.0%}"
            for h, stop, n, with_f, med, worst, stopped, b_avg, edge in shown)
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
